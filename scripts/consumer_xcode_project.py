#!/usr/bin/env python3
"""Build the native Xcode half of the public binary-layout smoke test."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import plistlib
import xml.etree.ElementTree as ET

MODULES = ('SwiftPythonRuntime', 'SwiftPythonEngine', 'Python',
           'SwiftPythonAudioInterop', 'SwiftPythonMetalInterop')


def generate(distribution, output):
    distribution, output = Path(distribution).resolve(strict=True), Path(output).resolve(strict=True)
    objects = {}

    def add(identity, isa, **values):
        key = hashlib.sha256(identity.encode()).hexdigest()[:24].upper()
        if key in objects:
            raise ValueError('Duplicate project object: ' + identity)
        objects[key] = dict(isa=isa, **values)
        return key

    source = output / 'Sources/ConsumerSmoke/ConsumerSmoke.swift'
    if not source.is_file():
        raise ValueError('Missing shared consumer source')
    source_ref = add('source', 'PBXFileReference', path=str(source), sourceTree='<absolute>', lastKnownFileType='sourcecode.swift')
    source_build = add('compile-source', 'PBXBuildFile', fileRef=source_ref)
    links, references, include_paths, frameworks = [], [source_ref], [], []
    for module in MODULES:
        bundle = distribution / (module + '.xcframework')
        info = plistlib.loads((bundle / 'Info.plist').read_bytes())
        slices = [item for item in info['AvailableLibraries']
                  if item['SupportedPlatform'] == 'macos' and not item.get('SupportedPlatformVariant')]
        if len(slices) != 1:
            raise ValueError('Expected one macOS slice: ' + module)
        item = slices[0]
        directory = bundle / item['LibraryIdentifier']
        library = directory / item['LibraryPath']
        if not library.exists() or not library.resolve().is_relative_to(bundle.resolve()):
            raise ValueError('Invalid binary input: ' + module)
        is_framework = library.suffix == '.framework'
        if is_framework:
            frameworks.append(str(directory))
        else:
            if not (directory / (module + '.swiftmodule')).is_dir():
                raise ValueError('Missing slice-root Swift module: ' + module)
            include_paths.append(str(directory))
        reference = add(module, 'PBXFileReference', path=str(library), sourceTree='<absolute>',
                        lastKnownFileType='wrapper.framework' if is_framework else 'archive.ar')
        references.append(reference)
        links.append(add('link-' + module, 'PBXBuildFile', fileRef=reference))
    source_phase = add('sources', 'PBXSourcesBuildPhase', buildActionMask=2147483647, files=[source_build], runOnlyForDeploymentPostprocessing=0)
    link_phase = add('frameworks', 'PBXFrameworksBuildPhase', buildActionMask=2147483647, files=links, runOnlyForDeploymentPostprocessing=0)
    product = add('product', 'PBXFileReference', explicitFileType='compiled.mach-o.executable', path='ConsumerSmoke', sourceTree='BUILT_PRODUCTS_DIR')
    products = add('products', 'PBXGroup', children=[product], name='Products', sourceTree='<group>')
    main = add('main', 'PBXGroup', children=[*references, products], sourceTree='<group>')
    settings = dict(SDKROOT='macosx', SUPPORTED_PLATFORMS='macosx', MACOSX_DEPLOYMENT_TARGET='15.0',
                    SWIFT_VERSION='6.0', SWIFT_OPTIMIZATION_LEVEL='-Onone',
                    PRODUCT_NAME='ConsumerSmoke', CODE_SIGNING_ALLOWED='NO', ALWAYS_SEARCH_USER_PATHS='NO',
                    SWIFT_INCLUDE_PATHS=include_paths, LIBRARY_SEARCH_PATHS=include_paths, FRAMEWORK_SEARCH_PATHS=frameworks,
                    LD_RUNPATH_SEARCH_PATHS=frameworks)
    target_config = add('target-config', 'XCBuildConfiguration', buildSettings=settings, name='Debug')
    target_configs = add('target-configs', 'XCConfigurationList', buildConfigurations=[target_config], defaultConfigurationIsVisible=0, defaultConfigurationName='Debug')
    target = add('target', 'PBXNativeTarget', buildConfigurationList=target_configs, buildPhases=[source_phase, link_phase],
                 buildRules=[], dependencies=[], name='ConsumerSmoke', productName='ConsumerSmoke', productReference=product,
                 productType='com.apple.product-type.tool')
    project_config = add('project-config', 'XCBuildConfiguration', buildSettings={}, name='Debug')
    project_configs = add('project-configs', 'XCConfigurationList', buildConfigurations=[project_config], defaultConfigurationIsVisible=0, defaultConfigurationName='Debug')
    project = add('project', 'PBXProject', attributes={}, buildConfigurationList=project_configs, compatibilityVersion='Xcode 14.0',
                  developmentRegion='en', hasScannedForEncodings=0, knownRegions=['en'], mainGroup=main,
                  productRefGroup=products, projectDirPath='', projectRoot='', targets=[target])
    project_dir = output / 'ConsumerSmoke.xcodeproj'
    project_dir.mkdir()
    (project_dir / 'project.pbxproj').write_bytes(plistlib.dumps(dict(archiveVersion='1', classes={}, objectVersion='56', objects=objects, rootObject=project)))
    scheme = ET.Element('Scheme', LastUpgradeVersion='1600', version='1.3')
    action = ET.SubElement(scheme, 'BuildAction', parallelizeBuildables='YES', buildImplicitDependencies='YES')
    entries = ET.SubElement(action, 'BuildActionEntries')
    entry = ET.SubElement(entries, 'BuildActionEntry', buildForTesting='YES', buildForRunning='YES', buildForProfiling='YES', buildForArchiving='YES', buildForAnalyzing='YES')
    ET.SubElement(entry, 'BuildableReference', BuildableIdentifier='primary', BlueprintIdentifier=target,
                  BuildableName='ConsumerSmoke', BlueprintName='ConsumerSmoke', ReferencedContainer='container:ConsumerSmoke.xcodeproj')
    schemes = project_dir / 'xcshareddata/xcschemes'
    schemes.mkdir(parents=True)
    ET.ElementTree(scheme).write(schemes / 'ConsumerSmoke.xcscheme', encoding='utf-8', xml_declaration=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('distribution')
    parser.add_argument('output')
    args = parser.parse_args()
    generate(args.distribution, args.output)
