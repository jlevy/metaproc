"""Skill artifact generation: spec, registry, and composition.

Skills are agent instruction files (SKILL.md) composed from package-source
baselines + dynamic catalog sections. They are generated artifacts — not
hand-edited — and gitignored at the project root. Workflow packages register
skills via the ``metaproc.skills`` entry-point group.
"""
