import re
import warnings
from pathlib import Path
from typing import TypeVar, Any, Dict
from urllib.parse import urlparse

from mlflow.artifacts import download_artifacts
from mlflow.exceptions import MlflowException
from mlflow.protos.databricks_pb2 import INVALID_PARAMETER_VALUE
from mlflow.store.artifact.artifact_repository_registry import get_registered_artifact_repositories
from mlflow.utils.annotations import experimental
from mlflow.utils.uri import is_local_uri


def register_artifact_dataset_sources():
    from mlflow.data.dataset_source_registry import register_dataset_source

    registered_source_schemes = set()
    artifact_schemes_to_exclude = [
        "http",
        "https",
        "runs",
        "models",
        "mlflow-artifacts",
        # DBFS supports two access patterns: dbfs:/ (URI) and /dbfs (FUSE).
        # The DBFS artifact repository online supports dbfs:/ (URI). To ensure
        # a consistent dictionary representation of DBFS datasets across the URI and
        # FUSE representations, we exclude dbfs from the set of dataset sources
        # that are autogenerated using artifact repositories and instead define
        # a separate DBFSDatasetSource elsewhere
        "dbfs",
    ]
    schemes_to_artifact_repos = get_registered_artifact_repositories()
    for scheme, artifact_repo in schemes_to_artifact_repos.items():
        if scheme in artifact_schemes_to_exclude or scheme in registered_source_schemes:
            continue

        if "ArtifactRepository" in artifact_repo.__name__:
            # Artifact repository name is something like "LocalArtifactRepository",
            # "S3ArtifactRepository", etc. To preserve capitalization, strip ArtifactRepository
            # and replace it with ArtifactDatasetSource
            dataset_source_name = artifact_repo.__name__.replace(
                "ArtifactRepository", "ArtifactDatasetSource"
            )
        else:
            # Artifact repository name has some other form, e.g. "dbfs_artifact_repo_factory".
            # In this case, generate the name by capitalizing the first letter of the scheme and
            # appending ArtifactRepository
            scheme = str(scheme)

            def camelcase_scheme(scheme):
                parts = re.split(r"[-_]", scheme)
                return "".join([part.capitalize() for part in parts])

            source_name_prefix = camelcase_scheme(scheme)
            dataset_source_name = f"{source_name_prefix}ArtifactDatasetSource"

        try:
            registered_source_schemes.add(scheme)
            dataset_source = _create_dataset_source_for_artifact_repo(
                scheme=scheme, dataset_source_name=dataset_source_name
            )
            register_dataset_source(dataset_source)
        except Exception as e:
            warnings.warn(
                f"Failed to register a dataset source for URIs with scheme '{scheme}': {e}",
                stacklevel=2,
            )


def _create_dataset_source_for_artifact_repo(scheme: str, dataset_source_name: str):
    from mlflow.data.filesystem_dataset_source import FileSystemDatasetSource

    if scheme in {"", "file"}:
        source_type = "local"
        class_docstring = "Represents the source of a dataset stored on the local filesystem."
    else:
        source_type = scheme
        class_docstring = (
            f"Represents a filesystem-based or blob-storage-based dataset source identified by a"
            f" URI with scheme '{scheme}'."
        )

    DatasetForArtifactRepoSourceType = TypeVar(dataset_source_name)



    @experimental
    class ArtifactRepoSource(FileSystemDatasetSource):
        def __init__(self, uri: str):
            self._uri = uri

        @property
        def uri(self):
            """
            The URI with scheme '{scheme}' referring to the dataset source filesystem location.

            :return: The URI with scheme '{scheme}' referring to the dataset source filesystem
                     location.
            """
            return self._uri

        @staticmethod
        def _get_source_type() -> str:
            return source_type

        def load(self, dst_path=None) -> str:
            """
            Downloads the dataset source to the local filesystem.

            :param dst_path: Path of the local filesystem destination directory to which to download
                             the dataset source. If the directory does not exist, it is created. If
                             unspecified, the dataset source is downloaded to a new uniquely-named
                             directory on the local filesystem, unless the dataset source already
                             exists on the local filesystem, in which case its local path is
                             returned directly.
            :return: The path to the downloaded dataset source on the local filesystem.
            """
            return download_artifacts(artifact_uri=self.uri, dst_path=dst_path)

        @staticmethod
        def _can_resolve(raw_source: Any):
            is_local_source_type = ArtifactRepoSource._get_source_type() == "local"

            if not isinstance(raw_source, str) and (
                not isinstance(raw_source, Path) and is_local_source_type
            ):
                return False

            try:
                if is_local_source_type:
                    return is_local_uri(str(raw_source), is_tracking_or_registry_uri=False)
                parsed_source = urlparse(str(raw_source))
                return parsed_source.scheme == scheme
            except Exception:
                return False

        @classmethod
        def _resolve(cls, raw_source: Any) -> DatasetForArtifactRepoSourceType:
            return cls(str(raw_source))

        def _to_dict(self) -> Dict[Any, Any]:
            """
            :return: A JSON-compatible dictionary representation of the {dataset_source_name}.
            """
            return {
                "uri": self.uri,
            }

        @classmethod
        def _from_dict(cls, source_dict: Dict[Any, Any]) -> DatasetForArtifactRepoSourceType:
            uri = source_dict.get("uri")
            if uri is None:
                raise MlflowException(
                    f'Failed to parse {dataset_source_name}. Missing expected key: "uri"',
                    INVALID_PARAMETER_VALUE,
                )

            return cls(uri=uri)


    ArtifactRepoSource.__name__ = dataset_source_name
    ArtifactRepoSource.__qualname__ = dataset_source_name
    ArtifactRepoSource.__doc__ = class_docstring
    ArtifactRepoSource._to_dict.__doc__ = ArtifactRepoSource._to_dict.__doc__.format(
        dataset_source_name=dataset_source_name
    )
    ArtifactRepoSource.uri.__doc__ = ArtifactRepoSource.uri.__doc__.format(scheme=scheme)
    return ArtifactRepoSource
