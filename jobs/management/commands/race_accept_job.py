from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import time

from django.core.management.base import BaseCommand
from django.db import close_old_connections

# Ajusta este import si tu path es distinto
from assignments import services as svc


@dataclass
class AttemptResult:
    status: str  # "OK" | "CONFLICT" | "ERROR"
    provider_id: int
    detail: str


def _attempt_once(job_id: int, provider_id: int, start_gate: threading.Barrier, sleep_ms: int) -> AttemptResult:
    try:
        close_old_connections()
        start_gate.wait(timeout=10)

        if sleep_ms > 0:
            time.sleep(sleep_ms / 1000.0)

        r = svc.activate_assignment_for_job(job_id=job_id, provider_id=provider_id)
        return AttemptResult("OK", provider_id, str(r))

    except Exception as e:
        # Si tu servicio lanza AssignmentConflict, lo detectamos por nombre (sin depender del import exacto)
        if e.__class__.__name__ == "AssignmentConflict":
            return AttemptResult("CONFLICT", provider_id, str(e))
        return AttemptResult("ERROR", provider_id, f"{e.__class__.__name__}: {e}")

    finally:
        close_old_connections()


class Command(BaseCommand):
    help = "Stress test: intenta activar/aceptar el mismo job concurrentemente para probar race condition."

    def add_arguments(self, parser):
        parser.add_argument("--job", type=int, required=True, help="Job ID a competir")
        parser.add_argument(
            "--providers",
            type=str,
            required=True,
            help="Lista de provider_ids separados por coma. Ej: 101,102,103,104",
        )
        parser.add_argument("--repeat", type=int, default=1, help="Cuantas rondas correr. Default: 1")
        parser.add_argument("--workers", type=int, default=0, help="Cantidad de hilos. 0 = igual al numero de providers.")
        parser.add_argument("--sleep-ms", type=int, default=0, help="Delay opcional por hilo despues del Barrier.")

    def handle(self, *args, **opts):
        job_id = opts["job"]
        providers = [int(x.strip()) for x in opts["providers"].split(",") if x.strip()]
        repeat = int(opts["repeat"])
        sleep_ms = int(opts["sleep_ms"])
        max_workers = int(opts["workers"]) or len(providers)

        self.stdout.write(
            self.style.WARNING(
                f"Race test -> job_id={job_id}, providers={providers}, repeat={repeat}, workers={max_workers}, sleep_ms={sleep_ms}"
            )
        )

        for round_no in range(1, repeat + 1):
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n=== ROUND {round_no}/{repeat} ==="))

            start_gate = threading.Barrier(parties=len(providers))
            results: list[AttemptResult] = []

            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = [ex.submit(_attempt_once, job_id, pid, start_gate, sleep_ms) for pid in providers]
                for f in as_completed(futures):
                    results.append(f.result())

            ok = [r for r in results if r.status == "OK"]
            conflict = [r for r in results if r.status == "CONFLICT"]
            err = [r for r in results if r.status == "ERROR"]

            self.stdout.write(self.style.SUCCESS(f"OK: {len(ok)}  CONFLICT: {len(conflict)}  ERROR: {len(err)}"))

            for r in ok:
                self.stdout.write(self.style.SUCCESS(f"  OK       pid={r.provider_id}  detail={r.detail}"))
            for r in conflict:
                self.stdout.write(self.style.WARNING(f"  CONFLICT pid={r.provider_id}  detail={r.detail}"))
            for r in err:
                self.stdout.write(self.style.ERROR(f"  ERROR    pid={r.provider_id}  detail={r.detail}"))
