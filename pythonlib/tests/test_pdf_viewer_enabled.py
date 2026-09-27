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


class TestNavigatorPlugins:
    """navigator.plugins overrides the plugin/MIME lists alone, for testing one
    half without the other; a pairing no real Firefox has is warned about."""

    @pytest.mark.parametrize(
        'config',
        [
            {'navigator.plugins': True},
            {'navigator.plugins': True, 'pdfViewerEnabled': True},
            {'navigator.plugins': False, 'pdfViewerEnabled': False},
        ],
    )
    def test_consistent_pairings_are_quiet(self, config, recwarn):
        launch(os='windows', config=config, i_know_what_im_doing=False)
        assert not [w for w in recwarn if 'navigator.plugins disagrees' in str(w.message)]

    @pytest.mark.parametrize(
        'config',
        [
            {'navigator.plugins': False},
            {'navigator.plugins': False, 'pdfViewerEnabled': True},
            {'navigator.plugins': True, 'pdfViewerEnabled': False},
        ],
    )
    def test_contradictions_are_warned(self, config, recwarn):
        launch(os='windows', config=config, i_know_what_im_doing=False)
        assert [w for w in recwarn if 'navigator.plugins disagrees' in str(w.message)]

    def test_plugins_alone_leaves_the_viewer_alone(self):
        config, prefs = launch(os='windows', config={'navigator.plugins': False})
        assert config['navigator.plugins'] is False
        assert 'pdfjs.disabled' not in prefs


class TestPrivacySignals:
    """doNotTrack and globalPrivacyControl are Firefox's own unless the caller
    sets them; BrowserForge's draw ("1" / true) is no longer copied in."""

    def test_generated_profiles_leave_them_to_firefox(self):
        for os_name in ('windows', 'macos', 'linux', 'android'):
            config, _ = launch(os=os_name)
            assert 'navigator.doNotTrack' not in config
            assert 'navigator.globalPrivacyControl' not in config

    def test_caller_values_are_passed_through(self):
        config, _ = launch(
            os='windows',
            config={'navigator.doNotTrack': '1', 'navigator.globalPrivacyControl': True},
        )
        assert config['navigator.doNotTrack'] == '1'
        assert config['navigator.globalPrivacyControl'] is True
