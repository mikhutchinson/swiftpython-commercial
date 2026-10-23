#!/usr/bin/env python3
"""Exercise VM interpreter security before the producer declares it ready."""
import argparse
import ensurepip
import io
import json
from pathlib import Path
import ssl
import subprocess
import sys
import tarfile
import tempfile


def handshake(client_context, server_context, hostname='localhost'):
    ci, co, si, so = (ssl.MemoryBIO() for _ in range(4))
    client = client_context.wrap_bio(ci, co, server_side=False, server_hostname=hostname)
    server = server_context.wrap_bio(si, so, server_side=True)
    client_done = server_done = False
    for _ in range(100):
        if not client_done:
            try:
                client.do_handshake()
                client_done = True
            except ssl.SSLWantReadError:
                pass
        data = co.read()
        if data:
            si.write(data)
        if not server_done:
            try:
                server.do_handshake()
                server_done = True
            except ssl.SSLWantReadError:
                pass
        data = so.read()
        if data:
            ci.write(data)
        if client_done and server_done:
            return
    raise RuntimeError('TLS handshake never reached a verdict')


def check(version, fixtures):
    assert sys.version_info[:3] == tuple(map(int, version.split('.')))
    assert ensurepip.version() == '26.2.2'
    import pip._vendor.certifi as certifi
    assert certifi.__version__ == '2026.10.19'
    ca_count = ssl.create_default_context().cert_store_stats()['x509_ca']
    assert ca_count > 0
    refusals = 0
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        outside = root / 'outside'
        outside.mkdir()
        for policy in ('tar', 'data'):
            for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
                for name in ('', '.', './', '/', 'a/..'):
                    archive_bytes = io.BytesIO()
                    destination = root / ('extract-' + str(refusals))
                    with tarfile.open(fileobj=archive_bytes, mode='w') as archive:
                        link = tarfile.TarInfo(name)
                        link.type = kind
                        link.linkname = str(outside)
                        archive.addfile(link)
                        item = tarfile.TarInfo('escaped')
                        item.size = 1
                        archive.addfile(item, io.BytesIO(b'x'))
                    archive_bytes.seek(0)
                    try:
                        with tarfile.open(fileobj=archive_bytes) as archive:
                            archive.extractall(destination, filter=policy)
                    except tarfile.FilterError:
                        refusals += 1
                    else:
                        raise RuntimeError('Destination link was accepted')
                    assert not (outside / 'escaped').exists()
            # A link elsewhere which originally resolves to the destination
            # can safely be replaced; do not regress the upstream correction.
            destination = root / ('healthy-' + policy)
            destination.mkdir()
            (destination / 'existing').symlink_to('.')
            archive_bytes = io.BytesIO()
            with tarfile.open(fileobj=archive_bytes, mode='w') as archive:
                member = tarfile.TarInfo('existing')
                member.type = tarfile.SYMTYPE
                member.linkname = 'target'
                archive.addfile(member)
            archive_bytes.seek(0)
            with tarfile.open(fileobj=archive_bytes) as archive:
                archive.extractall(destination, filter=policy)
            assert (destination / 'existing').readlink() == Path('target')
        compromised_server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        compromised_server.load_cert_chain(fixtures / 'distrusted_keycert.pem')
        for mode in (ssl.CERT_OPTIONAL, ssl.CERT_REQUIRED):
            client = ssl.create_default_context(cafile=str(fixtures / 'distrusted_root.pem'))
            client.verify_mode = mode
            try:
                handshake(client, compromised_server)
            except ssl.SSLCertVerificationError as error:
                assert error.verify_code == 28, error
            else:
                raise RuntimeError('Compromised SPKI was accepted with an old trusted root')
        # A verified, healthy custom authority still works, and hostname checks
        # still reject a certificate for the wrong name.
        key, certificate = root / 'key.pem', root / 'certificate.pem'
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'ec', '-pkeyopt',
                        'ec_paramgen_curve:P-256', '-nodes', '-days', '1',
                        '-subj', '/CN=localhost', '-addext', 'subjectAltName=DNS:localhost',
                        '-addext', 'basicConstraints=critical,CA:TRUE',
                        '-addext', 'keyUsage=critical,digitalSignature,keyCertSign,cRLSign',
                        '-keyout', str(key), '-out', str(certificate)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server.load_cert_chain(certificate, key)
        handshake(ssl.create_default_context(cafile=str(certificate)), server)
        try:
            handshake(ssl.create_default_context(cafile=str(certificate)), server, 'wrong.invalid')
        except ssl.SSLCertVerificationError as error:
            assert error.verify_code == 62, error
        else:
            raise RuntimeError('Healthy custom authority bypassed hostname verification')
        unverified = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        unverified.check_hostname = False
        unverified.verify_mode = ssl.CERT_NONE
        handshake(unverified, compromised_server)
    return dict(python=sys.version, openssl=ssl.OPENSSL_VERSION, executable=sys.executable,
                ensurepip=ensurepip.version(), certifi=certifi.__version__, ca_count=ca_count,
                tarRefusals=refusals, compromisedChainRefusals=2, healthyTLS=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', required=True)
    parser.add_argument('--fixtures', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    print(json.dumps(check(args.version, args.fixtures), sort_keys=True))
