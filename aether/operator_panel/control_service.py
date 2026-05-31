"""OperatorService — the privileged actions, decoupled from HTTP (spec §18.2).

Each method performs one operator action AND audits it (``operator_action`` event
with actor + timestamp), so the write surface is itself observable on Stargazer's
timeline and fully reconstructable. The HTTP layer (server.py) only adds auth +
request parsing on top of this.
"""
from __future__ import annotations

from typing import Optional

from ..core.aether_client import AetherClient
from ..core.control import ControlPlane
from ..core.envelope import new_envelope

OPERATOR_ACTOR = "operator"


class OperatorService:
    def __init__(self, client: AetherClient, control: ControlPlane,
                 actor: str = OPERATOR_ACTOR) -> None:
        self.client = client
        self.control = control
        self.actor = actor

    # ---- inject (operator as a sender identity) ---------------------------
    def inject(self, *, to: str, intent: str, text: str,
               conversation_id: Optional[str] = None, solicit: bool = False,
               max_hops: int = 8, from_: Optional[str] = None) -> dict:
        """Initiate a Comet or Wave as the operator. The receiving body still
        treats the body text as untrusted data (§18.2)."""
        env = new_envelope(from_=from_ or self.actor, to=to, intent=intent,
                           text=text, conversation_id=conversation_id,
                           solicit=solicit, max_hops=max_hops)
        self.client.emit(env)
        self.client.emit_operator_action(
            self.actor, "inject", conversation_id=env.conversation_id,
            to=to, message_id=env.message_id, wave=(env.type == "wave"),
            solicit=env.solicit)
        return {"message_id": env.message_id, "conversation_id": env.conversation_id,
                "type": env.type}

    # ---- pause / resume / terminate ---------------------------------------
    def pause(self, conversation_id: str) -> dict:
        self.control.pause(conversation_id)
        self.client.emit_operator_action(self.actor, "pause", conversation_id=conversation_id)
        return {"conversation_id": conversation_id, "state": "paused"}

    def resume(self, conversation_id: str) -> dict:
        self.control.resume(conversation_id)
        self.client.emit_operator_action(self.actor, "resume", conversation_id=conversation_id)
        return {"conversation_id": conversation_id, "state": "active"}

    def terminate(self, conversation_id: str) -> dict:
        """Force-extinguish a conversation (manual Horizon, §18.2)."""
        self.control.kill(conversation_id)
        # Audit + an immediate extinction marker so the 熄滅紀錄 shows it at once.
        self.client.emit_operator_action(self.actor, "terminate",
                                         conversation_id=conversation_id, reason="operator_kill")
        self.client.emit_event("terminated", env=None, reason="operator_kill",
                               conversation_id=conversation_id)
        return {"conversation_id": conversation_id, "state": "killed"}

    def kill_project(self, project_id: str) -> dict:
        """Per-project kill switch (§18.2)."""
        self.control.kill_project(project_id)
        self.client.emit_operator_action(self.actor, "kill_project", project_id=project_id,
                                         reason="operator_kill")
        return {"project_id": project_id, "state": "killed"}
