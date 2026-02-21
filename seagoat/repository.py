import datetime
import hashlib
import math
import subprocess
from collections import defaultdict
from pathlib import Path

from seagoat.gitfile import GitFile
from seagoat.utils.config import get_config_values
from seagoat.utils.file_reader import autodecode_bytes
from seagoat.utils.file_types import is_file_type_supported


def parse_commit_info(raw_line: str):
    commit_hash, date_str, author, commit_subject = raw_line.split(":::", 3)

    commit_date = datetime.datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S %z").date()
    today = datetime.date.today()
    days_passed = (today - commit_date).days

    return (commit_hash, days_passed, author, commit_subject)


class Repository:
    def __init__(self, repo_path: str):
        self.path = Path(repo_path)
        self.config = get_config_values(self.path)
        self.file_changes = defaultdict(list)
        self.frecency_scores = {}

    def _get_head_hash(self):
        return subprocess.check_output(
            ["git", "-C", str(self.path), "rev-parse", "HEAD"], text=True
        ).strip()

    def _get_working_tree_diff(self):
        return subprocess.check_output(
            ["git", "-C", str(self.path), "diff"], text=True
        ).strip()

    def _is_file_ignored(self, path: str):
        for pattern in self.config["server"]["ignorePatterns"]:
            if Path(path).match(pattern):
                return True

        return False

    def get_file_object_id(self, file_path: str):
        """
        Returns the git object id for the current version
        of a file
        """
        object_id = (
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(self.path),
                    "ls-tree",
                    "HEAD",
                    str(file_path),
                ],
                text=True,
            )
            .split()[2]
            .strip()
        )

        return object_id

    def _get_all_object_ids(self, filenames):
        """
        Batch fetch git object IDs for multiple files in a single subprocess call.
        Returns a dict mapping filename -> object_id.
        Files not present at HEAD are omitted.
        """
        if not filenames:
            return {}

        output = subprocess.check_output(
            ["git", "-C", str(self.path), "ls-tree", "HEAD", "--"] + list(filenames),
            text=True,
        )

        result = {}
        for line in output.splitlines():
            tab_pos = line.index("\t")
            filename = line[tab_pos + 1 :]
            object_id = line[:tab_pos].split()[2]
            result[filename] = object_id

        return result

    def get_blob_data(self, object_id: str) -> str:
        data = subprocess.check_output(
            ["git", "-C", str(self.path), "cat-file", "-p", object_id]
        )
        return autodecode_bytes(data)

    def is_up_to_date_git_object(self, file_path: str, git_object_id: str):
        return self.get_file_object_id(file_path) == git_object_id

    def get_status_hash(self):
        combined = self._get_head_hash() + self._get_working_tree_diff()
        return hashlib.sha256(combined.encode()).hexdigest()

    def analyze_files(self):
        cmd = [
            "git",
            "-C",
            self.path,
            "log",
            "--name-only",
            "--pretty=format:###%h:::%ai:::%an <%ae>:::%s",
            "--no-merges",
            *self._git_log_extra_options(),
        ]

        self.file_changes.clear()

        files = set(
            subprocess.check_output(["rg", "--files"], cwd=self.path, text=True).split()
        )

        current_commit_info = None
        with subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True) as proc:
            assert proc.stdout is not None
            for line in iter(proc.stdout.readline, ""):
                line = line.strip()
                if ":::" in line:
                    current_commit_info = parse_commit_info(line)
                elif line:
                    filename = line

                    if (
                        not is_file_type_supported(filename)
                        or self._is_file_ignored(filename)
                        or filename not in files
                    ):
                        continue

                    self.file_changes[filename].append(current_commit_info)

        self._compute_frecency()

    def _git_log_extra_options(self):
        cmd = []

        if (max_commits := self.config["server"]["readMaxCommits"]) is not None:
            cmd.append(f"--max-count={max_commits}")

        return cmd

    def _compute_frecency(self):
        self.frecency_scores = {}
        for file, commits in self.file_changes.items():
            score = sum(
                math.exp(-0.01 * days_passed)
                for _, days_passed, __, ___ in commits
            )
            self.frecency_scores[file] = score

    def top_files(self):
        sorted_files = sorted(
            self.frecency_scores.items(), key=lambda x: x[1], reverse=True
        )
        object_ids = self._get_all_object_ids([f for f, _ in sorted_files])
        return [
            (
                GitFile(
                    self,
                    filename,
                    str(self.path / filename),
                    object_ids[filename],
                    score,
                    [commit[3] for commit in self.file_changes[filename]],
                ),
                score,
            )
            for filename, score in sorted_files
            if filename in object_ids
        ]

    def get_file(self, filename: str):
        """
        Returns a GitFile object with the current version of the file
        """

        return GitFile(
            self,
            filename,
            str(self.path / filename),
            self.get_file_object_id(filename),
            self.frecency_scores[filename],
            [commit[3] for commit in self.file_changes[filename]],
        )
