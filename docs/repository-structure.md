# Repository structure

src/policyweave/ contains the five modules: policy_adder, back_translation,
policy_viewer, enforcement and analysis. core holds shared schemas and rule
semantics; adapters contains optional model/tool integrations; workbench composes
the CLI, UI and harness workflows.

demo/ contains the copied course example. examples/ provides smaller synthetic
inputs. tests/ contains offline checks and headless browser helpers.
install.sh prepares the local environment; pyproject.toml defines installation.

The course PDF and LaTeX source are at the repository root, with figures in
images/. No external submodule is needed to install or use this release.
