import os
import shutil

from git import Repo


REPOSITORY_DIRECTORY = os.path.expanduser(
    "~/ai-k8s-twin/temp_repository"
)


def clone_repository(github_url: str) -> str:

    if os.path.exists(REPOSITORY_DIRECTORY):

        print(
            "Repository already exists. Reusing it."
        )

        return REPOSITORY_DIRECTORY

    try:

        Repo.clone_from(
            github_url,
            REPOSITORY_DIRECTORY
        )

        return REPOSITORY_DIRECTORY

    except Exception:

        shutil.rmtree(
            REPOSITORY_DIRECTORY,
            ignore_errors=True
        )

        raise
