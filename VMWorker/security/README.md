# Owned guest Python security inputs

These are provisioning inputs, separate from the seven worker protocol files.
`install_runtime.py` verifies pinned downloads before extraction or execution,
builds into a dedicated prefix, and writes provenance after live verification.
The operating system's Python is retained as a bootstrap tool.

The tar backports come from CPython PRs 160342 (3.12, revision
98d9fffc41ac0aebc76d54ad1f9fcd375ecbeff6) and 160341 (3.13, revision
7a6bbf8ceb64aa4ffd12243f8ffbf0aad43e584b). The SPKI patch comes from PR 160042,
revision 9edaccc8916eda7baa78a0b774fa4a3b7563726f. The 3.12 adaptation preserves
its existing post-handshake callback handling and installs the rejection
callback in `_set_verify_mode`; the callback and eight hashes are unchanged.
All patches apply with zero fuzz. These backports are additional to the named
Python release; the release number alone does not attest them.

The distrusted PEM files are public CPython regression fixtures from that same
PR's `Lib/test/certdata/distrusted.zip`, distributed under the Python license.
The test private key is intentionally public, for a local test leaf only.
The verifier never contacts a third-party server. It requires rejection of the
compromised chain even when its old root is explicitly trusted, and separately
proves healthy local TLS and archive behavior.
