"""Read-only helper contracts; these tests never invoke WSL or download images."""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
import runpy
import tarfile
import tempfile
import unittest


HELPER = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'scripts' / 'wsl_lab.py'))


class WSLLabHelperTests(unittest.TestCase):
    def test_checksum_requires_exact_unambiguous_pinned_name(self):
        line = HELPER['IMAGE_SHA256'] + '  ' + HELPER['IMAGE']
        self.assertEqual(HELPER['checksum_entry'](line), HELPER['IMAGE_SHA256'])
        for text in ('', 'bad ' + HELPER['IMAGE'], line + '\n' + line,
                     HELPER['IMAGE_SHA256'] + ' another-image.tar.gz'):
            with self.assertRaises(ValueError):
                HELPER['checksum_entry'](text)

    def test_runtime_is_under_home_and_outside_repository(self):
        home = Path.home()
        repository = home / 'lsrp-source-fixture'
        expected = (home / 'lsrp-helper-fixture').resolve()
        self.assertEqual(HELPER['runtime_directory'](expected, repository), expected)
        for value in (home, repository, repository / 'artifacts', home.parent):
            with self.assertRaises(ValueError):
                HELPER['runtime_directory'](value, repository)

    def test_source_manifest_matches_transferred_bytes_and_excludes_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'lsrp').mkdir()
            (root / 'scripts').mkdir()
            (root / 'lsrp' / '__main__.py').write_bytes(b'print("fixture")\n')
            (root / 'scripts' / 'validate-linux.sh').write_bytes(b'#!/bin/bash\n')
            (root / 'lsrp' / '.env').write_bytes(b'SECRET=fixture\n')
            (root / 'artifacts').mkdir()
            (root / 'artifacts' / 'unrelated.txt').write_bytes(b'not source\n')
            payload, manifest = HELPER['source_archive'](root)
            self.assertEqual(set(manifest['files']), {'lsrp/__main__.py', 'scripts/validate-linux.sh'})
            with tarfile.open(fileobj=io.BytesIO(payload)) as archive:
                names = set(archive.getnames())
                self.assertEqual(names, set(manifest['files']) | {'SOURCE_MANIFEST.json', 'SOURCE_REVISION'})
                for name, expected in manifest['files'].items():
                    self.assertEqual(hashlib.sha256(archive.extractfile(name).read()).hexdigest(), expected)
                self.assertEqual(json.load(archive.extractfile('SOURCE_MANIFEST.json')), manifest)

    def archive(self, name, data=b'fixture\n', *, symlink=False):
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode='w') as archive:
            member = tarfile.TarInfo(name)
            if symlink:
                member.type = tarfile.SYMTYPE
                member.linkname = '/etc/shadow'
                archive.addfile(member)
            else:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
        return buffer.getvalue()

    def test_fetch_extracts_only_expected_regular_files_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'run'
            HELPER['extract_results'](self.archive('./environment.txt'), target)
            self.assertEqual((target / 'environment.txt').read_bytes(), b'fixture\n')
            with self.assertRaises(ValueError):
                HELPER['extract_results'](self.archive('./environment.txt'), target)

    def test_fetch_rejects_traversal_links_and_unexpected_files_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / 'run'
            for name, symlink in (('../escape', False), ('/absolute', False),
                                  ('environment.txt', True), ('private.key', False)):
                with self.assertRaises(ValueError):
                    HELPER['extract_results'](self.archive(name, symlink=symlink), target)
                self.assertFalse(target.exists())

    def test_run_names_cannot_escape_result_directory(self):
        self.assertEqual(HELPER['run_name']('validation-20261101T000000Z'), 'validation-20261101T000000Z')
        for value in ('../escape', '/', '', '..', 'test/child', 'space name', 'a' * 65):
            with self.assertRaises(ValueError):
                HELPER['run_name'](value)


if __name__ == '__main__':
    unittest.main()
