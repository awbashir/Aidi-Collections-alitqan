# Aidi Collections (CAWP)

Full notes: **`docs/PROJECT-NOTES.md`** (read before any change). Stack and run steps: `README.md`.

- **Extend, don't replace.** Add new blueprints, services, tables and screens beside the existing ones. Keep API wire contracts byte-compatible and prove parity with `tools/api_snapshot.py` before swapping an implementation. DB changes are additive scripts in `db/`.
- New infrastructure goes in `docker-compose.yml`.
- PBX is **Issabel** (Asterisk). Keep `issable/db_connection.py` and `/cdr*` and build calling on top of them.
- Browser SIP softphone: build our own in `CAWP-frontend` with similar UI and features (list in `docs/PROJECT-NOTES.md` §3.2). **Do not use or copy `CONNXTA/cloudsip.app`.** Calling logic and design evidence come from `awbashir/Aidi-Collections` (`docs/human-calling.md`, `docs/issabel-dialer.md`). Port the concepts, not its Node runtime.
- Click-to-call is extension-first. AMI "accepted" means submitted, not connected. Unknown stays unknown. Queue presence never authorizes a call.
- AI output is always a draft that a human saves. Pending, rejected and reversed money never counts as recovered. Asia/Riyadh dates.
- UI: Aidi AI Enterprise design system (`design-reference/`), Arabic RTL and English.
