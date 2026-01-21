from totelegram.core.enums import AvailabilityState, DuplicatePolicy
from totelegram.core.plans import *
from totelegram.services.discovery import DiscoveryReport


class PolicyExpert:
    @staticmethod
    def determine_plan(
        report: DiscoveryReport, policy: DuplicatePolicy
    ) -> ExecutionPlan:

        if report.state == AvailabilityState.SYSTEM_NEW:
            return PhysicalUploadPlan()

        # Planes para los casos de duplicidad

        if report.state == AvailabilityState.FULFILLED:
            return SkipPlan(
                reason="El archivo ya está íntegro en el chat destino.",
                is_already_fulfilled=True,
            )

        if report.state in [
            AvailabilityState.REMOTE_MIRROR,
            AvailabilityState.REMOTE_PUZZLE,
        ]:
            if policy == DuplicatePolicy.STRICT:
                return SkipPlan(reason=f"Omitido por política STRICT: chats.")

            if policy == DuplicatePolicy.OVERWRITE:
                return PhysicalUploadPlan(
                    reason="Forzando subida física según política OVERWRITE."
                )

            # Si es SMART, delegamos la pregunta al CLI
            return AskUserPlan(state=report.state, remotes=[])

        if report.state == AvailabilityState.REMOTE_RESTRICTED:
            if policy == DuplicatePolicy.OVERWRITE:
                return PhysicalUploadPlan(
                    reason="Subida física forzada por falta de acceso previo."
                )
            return AskUserPlan(state=report.state, remotes=[])

        return PhysicalUploadPlan()
