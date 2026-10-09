"""Ordinal context severity, separate from source ranking and clip-token coverage."""


def contextual_risk(context):
    if context["status"] != "completed" or context["assessment"] == "inconclusive":
        return None
    levels = {"none": 0, "low": 25, "moderate": 50, "high": 75}
    return max((levels[f["severity"]] for f in context["findings"]), default=0)
