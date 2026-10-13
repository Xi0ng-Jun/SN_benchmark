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
