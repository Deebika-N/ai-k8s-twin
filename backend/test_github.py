import os
import sys

sys.path.append(
    os.path.dirname(
        os.path.dirname(__file__)
    )
)

from services.github_service import clone_repository


github_url = input(
    "Enter GitHub repository URL: "
).strip()


try:

    repository_path = clone_repository(
        github_url
    )

    print("\nRepository cloned successfully!")
    print("Location:")
    print(repository_path)

    print("\nFiles in repository:")

    for item in os.listdir(repository_path):
        print(" -", item)

except Exception as e:

    print("\nFailed to clone repository.")
    print("Error:", e)