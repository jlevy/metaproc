"""Public runtime surface for metaproc code-mode handlers.

Domain packages should import handler-runtime helpers (template resolution,
named-IO path resolution, the CodeHandler protocol) from this subpackage
rather than reaching into ``metaproc.engine.*`` directly. The ``engine``
modules are private implementation detail and may change shape between
metaproc releases.
"""
