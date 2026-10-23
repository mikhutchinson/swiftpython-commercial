#!/usr/bin/env python3
"""Build an owned VM interpreter from pinned security inputs.

This provisioning tool never replaces the operating system's Python. It emits
its receipt only after checking the installed bytes and runtime behavior.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = Path(__file__).resolve().parent
PYTHONS = {
    '3.12': ('3.12.15', 'c2c4321961fab0fb999d66e0cecf521c2ab3994c7992873ea99e306c1094fd5a',
             'tar-3.12.patch', '98d9fffc41ac0aebc76d54ad1f9fcd375ecbeff6'),
    '3.13': ('3.13.17', '6020bd10aff3bf4821be16f088ebb1591beb7b811dcd8fab1da8f7c22a7b27e7',
             'tar-3.13.patch', '7a6bbf8ceb64aa4ffd12243f8ffbf0aad43e584b'),
}
CA = ('https://curl.se/ca/cacert-2026-10-20.pem',
      '796f1389bfea13c0ed78f663b765baaff54a508e5483786bd6ece73c5e3e3cbe')
PIP = ('https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl',
       'fd7f4dfa8190e014013b9f08f3e973f4e4c26ffd179a204895533a7254813a70')
SPKI_REVISION = '9edaccc8916eda7baa78a0b774fa4a3b7563726f'


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run(argv, **kwargs):
    print('+', ' '.join(map(str, argv)), flush=True)
    subprocess.run(list(map(str, argv)), check=True, **kwargs)


def download(url, expected, output):
    # Even the bootstrap machine may trust the compromised root. The independent
    # digest is mandatory before any downloaded bytes are extracted or executed.
    run(['curl', '--fail', '--location', '--retry', '3', '--proto', '=https',
         '--output', output, url])
    if digest(output) != expected:
        raise RuntimeError('Pinned input digest mismatch: ' + output.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--series', choices=sorted(PYTHONS), required=True)
    parser.add_argument('--prefix', type=Path, required=True)
    parser.add_argument('--system-packages', type=Path)
    parser.add_argument('--openssl-prefix', type=Path)
    parser.add_argument('--jobs', type=int, default=2)
    args = parser.parse_args()
    prefix = args.prefix.expanduser().resolve()
    if prefix.exists():
        raise RuntimeError('Owned runtime prefix already exists; refuse to replace it: ' + str(prefix))
    if args.jobs < 1 or not prefix.is_absolute() or prefix == Path('/'):
        raise RuntimeError('Invalid build configuration')
    version, source_hash, tar_patch, tar_revision = PYTHONS[args.series]
    system_packages = args.system_packages.resolve() if args.system_packages else None
    if system_packages is not None:
        if (sys.version_info[:2] != tuple(map(int, args.series.split('.')))
                or system_packages != Path('/usr/lib/python3/dist-packages')
                or not system_packages.is_dir()):
            raise RuntimeError('Distro packages require the matching CPython ABI and declared distro path')
    prefix.parent.mkdir(parents=True, exist_ok=True)
    recipe = {p.name: digest(p) for p in sorted(HERE.iterdir())
              if p.is_file()}
    with tempfile.TemporaryDirectory(prefix='swiftpython-runtime-build-', dir=prefix.parent) as scratch:
        work = Path(scratch)
        source_archive = work / ('Python-' + version + '.tar.xz')
        download('https://www.python.org/ftp/python/' + version + '/' + source_archive.name,
                 source_hash, source_archive)
        ca = work / 'cert.pem'
        download(*CA, ca)
        pip = work / 'pip-26.2.2-py3-none-any.whl'
        download(*PIP, pip)
        with zipfile.ZipFile(pip) as wheel:
            metadata = wheel.read('pip-26.2.2.dist-info/METADATA').decode()
            certifi = wheel.read('pip/_vendor/certifi/__init__.py').decode()
            if '\nVersion: 26.2.2\n' not in metadata or '2026.10.19' not in certifi:
                raise RuntimeError('Fixed pip/certifi identity missing')
        run(['tar', '-xf', source_archive, '-C', work])
        source = work / ('Python-' + version)
        spki_patch = 'distrusted-spki-3.12.patch' if args.series == '3.12' else 'distrusted-spki.patch'
        for patch in (tar_patch, spki_patch):
            run(['patch', '--batch', '--fuzz=0', '-p1', '-i', HERE / patch], cwd=source)
        bundled = source / 'Lib/ensurepip/_bundled'
        for old in bundled.glob('pip-*.whl'):
            old.unlink()
        shutil.copy2(pip, bundled / pip.name)
        # Both branches derive ensurepip's wheel from this declared version.
        ensurepip = source / 'Lib/ensurepip/__init__.py'
        content = ensurepip.read_text()
        import re
        content, replacements = re.subn(r'_PIP_VERSION = "[^"]+"', '_PIP_VERSION = "26.2.2"', content)
        if replacements != 1:
            raise RuntimeError('Unexpected ensurepip version declaration')
        ensurepip.write_text(content)
        configure = [str(source / 'configure'), '--prefix=' + str(prefix),
                     '--with-ensurepip=install', '--enable-loadable-sqlite-extensions']
        if args.openssl_prefix:
            configure.extend(['--with-openssl=' + str(args.openssl_prefix.resolve()),
                              '--with-openssl-rpath=auto'])
        env = os.environ.copy()
        env.pop('PYTHONHOME', None)
        env.pop('PYTHONPATH', None)
        env['PYTHONDONTWRITEBYTECODE'] = '1'
        run(configure, cwd=source, env=env)
        run(['make', '-j' + str(args.jobs)], cwd=source, env=env)
        run(['make', 'install'], cwd=source, env=env)
        python = prefix / 'bin/python3'
        ssl_dir = prefix / 'ssl'
        ssl_dir.mkdir()
        shutil.copy2(ca, ssl_dir / 'cert.pem')
        site = prefix / ('lib/python' + args.series + '/site-packages')
        if system_packages is not None:
            (site / 'swiftpython-distro-packages.pth').write_text(str(system_packages) + '\n')
        env.update(SSL_CERT_FILE=str(ssl_dir / 'cert.pem'),
                   REQUESTS_CA_BUNDLE=str(ssl_dir / 'cert.pem'))
        completed = subprocess.run([str(python), '-B', '-I', str(HERE / 'verify_runtime.py'),
                                    '--version', version], env=env,
                                   check=True, text=True, capture_output=True)
        observation = json.loads(completed.stdout)
        # Bind all installed files, including the extension implementing SPKI
        # rejection and ensurepip's vendored certificate bundle.
        files = {p.relative_to(prefix).as_posix(): digest(p)
                 for p in sorted(prefix.rglob('*')) if p.is_file() and not p.is_symlink()}
        receipt = dict(schema=1, python=version, sourceSHA256=source_hash,
                       tarRevision=tar_revision, spkiRevision=SPKI_REVISION,
                       caSHA256=CA[1], pipSHA256=PIP[1], recipe=recipe,
                       systemPackages=str(system_packages) if system_packages else None,
                       observation=observation, files=files)
        (prefix / 'swiftpython-runtime.json').write_text(json.dumps(receipt, indent=2, sort_keys=True) + '\n')
        print('Verified owned runtime:', completed.stdout.strip(), flush=True)


if __name__ == '__main__':
    main()
