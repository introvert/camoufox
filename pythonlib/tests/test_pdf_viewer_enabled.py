"""
pdfViewerEnabled has to move navigator.pdfViewerEnabled, navigator.plugins and
navigator.mimeTypes together, both ways (pdf-viewer-spoofing.patch; the browser
half is tests/patches/pdf-viewer-enabled.py). The launcher's half is to keep the
real PDF viewer in step through the pdfjs.disabled pref.

Run with:
    cd pythonlib && python -m pytest tests/test_pdf_viewer_enabled.py -v
"""

import pytest

from test_mobile_mode import launch


class TestPdfViewerEnabled:
    """pdfViewerEnabled is answered in C++ (pdf-viewer-spoofing.patch); the
    launcher keeps the real viewer in step through pdfjs.disabled."""

    @pytest.mark.parametrize('enabled', [True, False])
    def test_viewer_pref_follows_the_config(self, enabled):
        config, prefs = launch(os='windows', config={'pdfViewerEnabled': enabled})
        assert config['pdfViewerEnabled'] is enabled
        assert prefs['pdfjs.disabled'] is (not enabled)

    def test_unset_leaves_the_viewer_alone(self):
        config, prefs = launch(os='windows')
        assert 'pdfViewerEnabled' not in config
        assert 'pdfjs.disabled' not in prefs

    def test_explicit_pref_wins(self):
        _, prefs = launch(
            os='windows',
            config={'pdfViewerEnabled': False},
            firefox_user_prefs={'pdfjs.disabled': False},
        )
        assert prefs['pdfjs.disabled'] is False
