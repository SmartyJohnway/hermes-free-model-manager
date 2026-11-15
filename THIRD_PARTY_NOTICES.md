# Third-party software and license notices

Hermes Free Model Manager's original source code is licensed under MIT (see `LICENSE`). The Windows portable ZIP additionally contains a Python 3.13.7 runtime and third-party libraries. Their upstream licenses remain applicable.

## Redistributed license texts

The release includes `THIRD_PARTY_LICENSES/` with a package/version inventory (`inventory.json`) and copies of upstream license notices collected from the installed Python distribution metadata. These cover Python (PSF), PyYAML, httpx, httpcore, anyio, certifi, idna, h11, sniffio, NumPy (including its bundled OpenBLAS notice), attrs, click, packaging, setuptools and vendored components, Pillow, charset-normalizer, requests, urllib3, colorama, python-dateutil, six, pydantic, rich, and packaging tools PyInstaller and related components. Some listed packages are build-time or transitive dependencies and may not all be included in the final binary; they are included conservatively to preserve attribution.

- Python: PSF License, plus component notices in the Python license file.
- PyInstaller: GPL-2.0-or-later with a special exception for distributing bundled applications; the exception and full upstream license text are preserved.
- certifi: MPL-2.0; the certificate bundle is supplied unmodified.
- NumPy: BSD-3-Clause and included third-party component notices, including OpenBLAS.
- Other components: see the exact upstream license texts and versions in `THIRD_PARTY_LICENSES/inventory.json`.

This application is **unofficial** and not affiliated with or endorsed by NousResearch or Hermes Agent. Hermes Agent itself is not bundled.

## Binary distribution notes

Windows system DLLs and Python runtime modules may carry additional notices documented by the Python distribution and Microsoft runtime. The binary is unsigned. No upstream trademark rights are granted by this repository.

For license questions, consult the individual files under `THIRD_PARTY_LICENSES/` rather than treating this summary as a substitute for the licenses.
