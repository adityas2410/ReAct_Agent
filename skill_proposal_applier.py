import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class SkillProposalApplier:
    """
    Shows Skill proposals to the user, asks for approval, then applies or deletes them.
    """

    def __init__(self, skills_dir: str = "skills") -> None:
        self.skills_dir = Path(skills_dir)

    def review(self, proposal_path: str) -> dict[str, Any]:
        path = Path(proposal_path)
        try:
            proposal = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return {
                "status": "review_failed",
                "proposal_path": str(path),
                "error": f"{type(exc).__name__}: {exc}",
            }

        self._print_proposal(proposal, path)
        if not self._approved():
            path.unlink(missing_ok=True)
            print(f"Skill proposal rejected and deleted: {path}")
            return {
                "status": "rejected_deleted",
                "proposal_path": str(path),
                "skill": proposal.get("skill"),
                "type": proposal.get("type"),
            }

        try:
            target_path = self._apply(proposal)
            applied_proposal = {
                **proposal,
                "status": "applied",
                "applied_at": datetime.now(timezone.utc).isoformat(),
                "target_path": str(target_path),
            }
            path.write_text(json.dumps(applied_proposal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            print(f"Skill proposal applied: {target_path}")
            return {
                "status": "applied",
                "proposal_path": str(path),
                "target_path": str(target_path),
                "skill": applied_proposal.get("skill"),
                "type": applied_proposal.get("type"),
            }
        except Exception as exc:
            print(f"Skill proposal could not be applied and was kept for inspection: {path}")
            return {
                "status": "apply_failed",
                "proposal_path": str(path),
                "skill": proposal.get("skill"),
                "type": proposal.get("type"),
                "error": f"{type(exc).__name__}: {exc}",
            }

    def _print_proposal(self, proposal: dict[str, Any], path: Path) -> None:
        print("\nSkill proposal generated")
        print(f"Path: {path}")
        print(f"Type: {proposal.get('type', 'skill_update')}")
        print(f"Target Skill: {proposal.get('skill', 'general')}")
        print(f"Reason: {proposal.get('reason', '')}")
        print("Suggested change:")
        print(str(proposal.get("suggested_change", "")).strip() or "- No suggested change provided.")

    def _approved(self) -> bool:
        try:
            answer = input("Apply this Skill change now? [y/N]: ").strip().lower()
        except EOFError:
            return False
        return answer in {"y", "yes"}

    def _apply(self, proposal: dict[str, Any]) -> Path:
        proposal_type = str(proposal.get("type") or "skill_update")
        skill_name = self._slug(str(proposal.get("skill") or "general"))
        suggested_change = str(proposal.get("suggested_change") or "").strip()
        if not suggested_change:
            raise ValueError("Proposal has no suggested_change")

        if proposal_type == "missing_capability":
            return self._create_or_update_missing_skill(skill_name, suggested_change)

        return self._append_learned_update(skill_name, suggested_change)

    def _append_learned_update(self, skill_name: str, suggested_change: str) -> Path:
        path = self._skill_path(skill_name)
        if not path.exists():
            return self._create_skill(
                skill_name=skill_name,
                description="Created from an approved Skill update proposal.",
                suggested_change=suggested_change,
            )

        content = path.read_text(encoding="utf-8").rstrip()
        entry = f"- {suggested_change}"
        if "\n## Learned Updates" in content:
            content = f"{content}\n{entry}\n"
        else:
            content = f"{content}\n\n## Learned Updates\n\n{entry}\n"
        path.write_text(content, encoding="utf-8")
        return path

    def _create_or_update_missing_skill(self, skill_name: str, suggested_change: str) -> Path:
        path = self._skill_path(skill_name)
        if path.exists():
            return self._append_learned_update(skill_name, suggested_change)

        return self._create_skill(
            skill_name=skill_name,
            description="Created from an approved missing capability proposal.",
            suggested_change=suggested_change,
        )

    def _create_skill(self, skill_name: str, description: str, suggested_change: str) -> Path:
        path = self._skill_path(skill_name)
        path.parent.mkdir(parents=True, exist_ok=True)
        triggers = self._triggers_for(skill_name)
        title = skill_name.replace("_", " ").title()
        content = (
            "---\n"
            f"name: {skill_name}\n"
            f"description: {description}\n"
            f"triggers: {json.dumps(triggers)}\n"
            "tools: []\n"
            "---\n\n"
            f"# {title}\n\n"
            "This Skill was created from approved run experience. Add MCP tools when execution support exists.\n\n"
            "## Learned Updates\n\n"
            f"- {suggested_change}\n"
        )
        path.write_text(content, encoding="utf-8")
        return path

    def _skill_path(self, skill_name: str) -> Path:
        return self.skills_dir / skill_name / "SKILL.md"

    def _slug(self, value: str) -> str:
        chars: list[str] = []
        previous_underscore = False
        for char in value.lower().strip():
            if char.isalnum():
                chars.append(char)
                previous_underscore = False
            elif not previous_underscore:
                chars.append("_")
                previous_underscore = True

        slug = "".join(chars).strip("_")
        return slug or "general"

    def _triggers_for(self, skill_name: str) -> list[str]:
        parts = [part for part in skill_name.split("_") if part]
        triggers = [skill_name, *parts]
        deduped: list[str] = []
        for trigger in triggers:
            if trigger not in deduped:
                deduped.append(trigger)
        return deduped
