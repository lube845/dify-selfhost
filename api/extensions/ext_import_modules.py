from dify_app import DifyApp


def init_app(app: DifyApp):
    # Side-effect imports that must run before request handling starts.
    from events import event_handlers  # noqa: F401

    # Monkey-patch the graphon document-extractor node so .wps / .et / .doc
    # files route through LibreOffice with the correct infilter hint and a
    # content-based fallback. Done here (early, before any workflow runs) so
    # the patched helper is in place by the first extraction request.
    from extensions.ext_graphon_wps_patch import install as _install_wps_patch

    _install_wps_patch()
