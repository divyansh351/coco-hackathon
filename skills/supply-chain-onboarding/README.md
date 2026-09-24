# Setting up the `supply-chain-onboarding` skill

This directory (`skills/supply-chain-onboarding/`) is a portable copy of a
[Cortex Code](https://docs.snowflake.com/en/user-guide/cortex-code/cortex-code)
skill. Cortex Code (CoCo) only picks up skills from specific local
directories, not directly from a cloned repo, so you need to copy this
folder into one of those locations once.

## What a CoCo skill is

A skill is a folder containing a `SKILL.md` (frontmatter + instructions) plus
any supporting reference files. CoCo loads a skill's instructions into context
when the user's request matches the `description` in the frontmatter, or when
explicitly invoked with `/supply-chain-onboarding` or `$supply-chain-onboarding`.
There is no separate "install" or "register" step beyond putting the files in
a directory CoCo scans.

## Setup: project-local (recommended for this repo)

Available only inside this project's CoCo sessions.

```bash
mkdir -p .cortex/skills/supply-chain-onboarding
cp skills/supply-chain-onboarding/SKILL.md .cortex/skills/supply-chain-onboarding/
cp skills/supply-chain-onboarding/reference.md .cortex/skills/supply-chain-onboarding/
```

(`.cortex/` is git-ignored in this repo by convention -- local tool state
shouldn't be committed -- which is why the portable copy lives under
`skills/` instead and needs this one-time copy step.)

## Setup: global (available across all your projects)

```bash
mkdir -p "$HOME/.snowflake/cortex/skills/supply-chain-onboarding"
cp skills/supply-chain-onboarding/*.md "$HOME/.snowflake/cortex/skills/supply-chain-onboarding/"
```

If `$SNOWFLAKE_HOME` is set, use `$SNOWFLAKE_HOME/cortex/skills/` instead of
`$HOME/.snowflake/cortex/skills/`.

On Windows (PowerShell):

```powershell
New-Item -ItemType Directory -Force ".cortex\skills\supply-chain-onboarding"
Copy-Item "skills\supply-chain-onboarding\*.md" ".cortex\skills\supply-chain-onboarding\"
```

## Verify it's picked up

Start a new CoCo session in this project (or globally, if installed
globally) and type `/` -- `supply-chain-onboarding` should appear in the
skill picker. No restart or registration command is needed beyond the copy.

## Using it

Once installed, either:
- Ask naturally: "onboard `SC_DEMO.RAW` onto the supply chain semantic layer"
  -- the skill's `description` frontmatter is matched against your request
  and it loads automatically, or
- Invoke it explicitly: `$supply-chain-onboarding` or `/supply-chain-onboarding`.

The skill will ask for (or expects you to state) the source database/schema
and target database/schema/view name, then run `framework/onboard.py`
end-to-end and report the mapping + validation results. See `SKILL.md` in
this folder for the exact workflow, prerequisites (an `AI_COMPLETE` grant and
a PAT-based connection profile), and known limitations.

## Keeping the two copies in sync

If you edit the skill's behavior, edit the installed copy
(`.cortex/skills/supply-chain-onboarding/` or the global path) first while
iterating, then copy the final version back into `skills/supply-chain-onboarding/`
in this repo so it stays committed and shareable.
