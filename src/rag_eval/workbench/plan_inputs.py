"""Read complete canonical Notebook inputs through server-registered locators.

Descriptions expose public case/partition ownership. Frozen plans retain only
input identity and an explicit selection; paths, questions, gold and materials
never enter either response. This adapter never writes the registered source.
source_revision is 'sha256:' plus the fingerprint of the original revision
string: a content identity, not its original text, label or source locator.
"""
from __future__ import annotations

from pathlib import Path
import re

from ..identity import fingerprint
from ..notebook_bundle import load_bundle
from .repository import Conflict


ADAPTER = 'notebook-bundle'
ADAPTATION = 'notebook-data-v3'


def _id(value, field):
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ValueError('Invalid ' + field)
    return value


def _ids(values, field):
    if not isinstance(values, list) or not values:
        raise ValueError('Invalid ' + field)
    result = [_id(value, field) for value in values]
    if len(set(result)) != len(result):
        raise ValueError('Duplicate ' + field)
    return result


def _bundle_id(value):
    if not isinstance(value, str) or re.fullmatch('[0-9a-f]{64}', value) is None:
        raise ValueError('Invalid expected bundle ID')
    return value


class NotebookPlanInputs:
    """A trusted server mapping of input_id -> complete bundle directory.

    Relative paths resolve at registration. The caller that reads configuration
    must resolve them against its configuration directory before registering.
    No result snapshot or caller-supplied path is an input selector.
    """

    def __init__(self, locators):
        if not isinstance(locators, dict):
            raise ValueError('Invalid input registration')
        self._locators = {}
        for input_id, directory in locators.items():
            _id(input_id, 'input ID')
            if not isinstance(directory, (str, Path)) or not str(directory):
                raise ValueError('Invalid bundle locator')
            self._locators[input_id] = Path(directory).resolve()

    def _describe(self, input_id):
        _id(input_id, 'input ID')
        if input_id not in self._locators:
            raise ValueError('Unknown input ID')
        directory = self._locators[input_id]
        try:
            if (directory / 'snapshot.json').exists() or (directory / 'COMMIT.json').exists():
                raise ValueError('Result snapshots are not canonical inputs')
            bundle = load_bundle(directory)  # Full source rebuild, not a subset snapshot.
        except FileNotFoundError:
            raise FileNotFoundError('Notebook input source is unavailable') from None
        except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
            # Loader/OS diagnostics can contain private filenames or raw values.
            raise ValueError('Invalid canonical notebook bundle') from None
        manifest = bundle['manifest']
        if manifest.get('adaptation_revision') != ADAPTATION:
            raise ValueError('Notebook plan input requires notebook-data-v3')
        cases = _ids([case['case_id'] for case in bundle['cases']], 'canonical case IDs')
        _ids([part['partition_id'] for part in bundle['partitions']], 'canonical partition IDs')
        case_set, owned_cases = set(cases), set()
        partitions = []
        for part in bundle['partitions']:
            owned = _ids(part['case_ids'], 'partition case IDs')
            if not set(owned) <= case_set or owned_cases.intersection(owned):
                raise ValueError('Invalid canonical partition ownership')
            owned_cases.update(owned)
            partitions.append(dict(partition_id=part['partition_id'], case_ids=owned))
        if owned_cases != case_set:
            raise ValueError('Incomplete canonical partition ownership')
        revision = manifest['source']['revision']
        if not isinstance(revision, str) or not revision.strip():
            raise ValueError('Invalid source revision')
        return dict(input_id=input_id, adapter=ADAPTER, bundle_id=fingerprint(manifest),
                    suite=manifest['suite'], source_revision='sha256:' + fingerprint(revision),
                    adaptation_revision=ADAPTATION, case_ids=cases, partitions=partitions)

    def describe(self, input_id):
        """Public identity and complete case/partition catalogue for selection."""
        return self._describe(input_id)

    def canonical(self, input_id, *, case_ids, expected_bundle_id):
        """Freeze explicit cases and their derived partition IDs in one read.

        Partition IDs denote material ownership, not a reduced material list.
        Future execution must consume the complete canonical partition materials.
        """
        expected_bundle_id = _bundle_id(expected_bundle_id)
        requested = _ids(case_ids, 'case IDs')
        description = self._describe(input_id)
        if expected_bundle_id != description['bundle_id']:
            raise Conflict('Expected bundle identity changed')
        selected = set(requested)
        if not selected <= set(description['case_ids']):
            raise ValueError('Selection is outside the canonical bundle')
        selection = dict(kind='explicit',
                         case_ids=[cid for cid in description['case_ids'] if cid in selected],
                         partition_ids=[part['partition_id'] for part in description['partitions']
                                        if selected.intersection(part['case_ids'])])
        selection['selection_hash'] = fingerprint(selection)
        identity = {key: value for key, value in description.items()
                    if key not in {'case_ids', 'partitions'}}
        return dict(input=identity, selection=selection)

    def selection(self, input_id, *, case_ids, expected_bundle_id):
        """Return only the explicit, canonical selection with derived partitions."""
        return self.canonical(input_id, case_ids=case_ids,
                              expected_bundle_id=expected_bundle_id)['selection']
