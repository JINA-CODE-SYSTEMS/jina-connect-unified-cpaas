"""
WhatsApp Data Models

This package contains Pydantic models and data structures for WhatsApp API interactions.

Sub-packages:
- gupshup: Data models for Gupshup BSP API
- meta_direct: Data models for META Direct API
- wati: Data models for WATI BSP API

Usage:
    # Import from specific BSP
    from wa.utility.data_model.gupshup import TemplateInput, SessionMessageBase
    from wa.utility.data_model.meta_direct import SomeMetaModel
    from wa.utility.data_model.wati import WATITemplateInput, WATIMessageInput
    
    # Or import specific modules
    from wa.utility.data_model.gupshup.template_button_input import parse_template_buttons
    from wa.utility.data_model.wati.template_input import WATITemplateInput
"""
