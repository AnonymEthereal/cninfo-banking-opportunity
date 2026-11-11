def test_app_dependencies_are_importable():
    import streamlit
    assert streamlit is not None
