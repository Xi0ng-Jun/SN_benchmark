"""Shared validation of captured-source identities at read/load boundaries."""
from .identity import fingerprint


def captured_source_manifest(identity):
    """Require the actual manifest and its content digest, never provenance alone."""
    if not isinstance(identity, dict) or identity.get('source_format') != 'sn-source-capture-v1':
        raise ValueError('Invalid captured source identity')
    manifest = identity.get('actual_manifest')
    if (not isinstance(manifest, dict) or not manifest
            or identity.get('source_manifest_sha256') != fingerprint(manifest)):
        raise ValueError('Captured source manifest is incomplete or inconsistent')
    return manifest


def compact_code_identity(code):
    """Code identity without the per-file manifest; ``source_manifest_sha256`` keeps its digest.

    Runs written before this change carry the whole manifest; it must still
    agree with the digest, so dropping it loses nothing that was verifiable.
    """
    if not isinstance(code, dict) or 'actual_manifest' not in code:
        return code
    manifest = code['actual_manifest']
    if not isinstance(manifest, dict) or code.get('source_manifest_sha256') != fingerprint(manifest):
        raise ValueError('Captured source manifest is incomplete or inconsistent')
    return {key: value for key, value in code.items() if key != 'actual_manifest'}
