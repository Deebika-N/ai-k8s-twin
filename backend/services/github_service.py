import shutil
import tempfile

from git import Repo


def clone_repository(github_url: str) -> str:
    """
    Clone a GitHub repository into a temporary directory.

    Returns:
        Path to the cloned repository.
    """

    temp_dir = tempfile.mkdtemp(
        prefix="ai_k8s_twin_"
    )

    try:
        Repo.clone_from(
            github_url,
            temp_dir
        )

        return temp_dir

    except Exception:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )

        raise