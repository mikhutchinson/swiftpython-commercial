#!/usr/bin/env python3
import plistlib
from pathlib import Path
import tempfile
import unittest

from consumer_xcode_project import MODULES, generate


class ConsumerProjectTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.distribution = self.root / 'Binaries with spaces'
        self.output = self.root / 'Consumer'
        source = self.output / 'Sources/ConsumerSmoke/ConsumerSmoke.swift'
        source.parent.mkdir(parents=True)
        source.write_text('import SwiftPythonRuntime\n')
        for module in MODULES:
            bundle = self.distribution / (module + '.xcframework')
            directory = bundle / 'macos-arm64_x86_64'
            directory.mkdir(parents=True)
            library = module + '.framework' if module in ('SwiftPythonEngine', 'Python') else 'lib' + module + '.a'
            if library.endswith('.framework'):
                (directory / library).mkdir()
            else:
                (directory / library).write_bytes(b'fixture')
                (directory / (module + '.swiftmodule')).mkdir()
            (bundle / 'Info.plist').write_bytes(plistlib.dumps({'AvailableLibraries': [dict(
                SupportedPlatform='macos', SupportedArchitectures=['arm64', 'x86_64'],
                LibraryIdentifier=directory.name, LibraryPath=library)]}))

    def test_one_link_per_binary_and_slice_root_lookup(self):
        generate(self.distribution, self.output)
        value = plistlib.loads((self.output / 'ConsumerSmoke.xcodeproj/project.pbxproj').read_bytes())
        objects = value['objects']
        links = next(o for o in objects.values() if o['isa'] == 'PBXFrameworksBuildPhase')['files']
        paths = [objects[objects[key]['fileRef']]['path'] for key in links]
        self.assertEqual(len(paths), 5)
        self.assertEqual(len(set(paths)), 5)
        settings = next(o['buildSettings'] for o in objects.values() if o['isa'] == 'XCBuildConfiguration' and o['buildSettings'])
        self.assertEqual(settings['SUPPORTED_PLATFORMS'], 'macosx')
        self.assertEqual(settings['SDKROOT'], 'macosx')
        self.assertEqual(settings['LIBRARY_SEARCH_PATHS'], settings['SWIFT_INCLUDE_PATHS'])
        self.assertEqual(len(settings['SWIFT_INCLUDE_PATHS']), 3)
        self.assertTrue(all(Path(p).name == 'macos-arm64_x86_64' for p in settings['SWIFT_INCLUDE_PATHS']))
        self.assertNotIn('OTHER_LDFLAGS', settings)

    def test_headers_only_layout_cannot_pass_native_gate(self):
        root = self.distribution / 'SwiftPythonRuntime.xcframework/macos-arm64_x86_64'
        (root / 'Headers').mkdir()
        (root / 'SwiftPythonRuntime.swiftmodule').rename(root / 'Headers/SwiftPythonRuntime.swiftmodule')
        with self.assertRaisesRegex(ValueError, 'Missing slice-root'):
            generate(self.distribution, self.output)

    def test_ambiguous_platform_and_escaped_binary_are_rejected(self):
        path = self.distribution / 'Python.xcframework/Info.plist'
        original = plistlib.loads(path.read_bytes())
        value = dict(original, AvailableLibraries=original['AvailableLibraries'] * 2)
        path.write_bytes(plistlib.dumps(value))
        with self.assertRaisesRegex(ValueError, 'one macOS slice'):
            generate(self.distribution, self.output)
        original['AvailableLibraries'][0]['LibraryPath'] = str(self.output)
        path.write_bytes(plistlib.dumps(original))
        with self.assertRaisesRegex(ValueError, 'Invalid binary input'):
            generate(self.distribution, self.output)


if __name__ == '__main__':
    unittest.main()
