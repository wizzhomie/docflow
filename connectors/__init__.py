"""Active CRM connector selection.

The review flow asks for the active connector once, so a client can switch CRM
by changing one environment variable instead of editing the application.
"""

import os

from connectors.uspacy_connector import UspacyConnector


def provider_choice() -> str:
    return (os.environ.get('DOCFLOW_CRM') or 'uspacy').strip().lower()


def active():
    choice = provider_choice()
    if choice in {'none', 'off', 'disabled'}:
        return None
    if choice in {'bitrix24', 'bitrix'}:
        from connectors.bitrix24 import Bitrix24Connector

        return Bitrix24Connector()
    return UspacyConnector()


def provider_name() -> str:
    connector = active()
    return connector.name if connector is not None else 'none'
