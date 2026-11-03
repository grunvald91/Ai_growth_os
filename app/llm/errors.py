"""Provider errors that can safely trigger model fallback without masking application bugs."""


class ProviderEmptyResponseError(RuntimeError):
    """The API completed, but no usable assistant answer was returned."""


class ProviderMalformedResponseError(RuntimeError):
    """The provider returned an invalid or structurally incomplete response."""
