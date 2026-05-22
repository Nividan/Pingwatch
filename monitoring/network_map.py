import sqlite3
import json
import os
from contextlib import contextmanager

from core.config import DB_PATH as _DB_PATH
from core.logger import log
from db.backend  import is_pg


@contextmanager
def _conn():
    """Open a SQLite connection, commit on success / rollback on failure,
    and ALWAYS close. The bare `sqlite3.Connection.__exit__` only commits,
    so callers using `with _conn() as con:` were leaking handles."""
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_topo_db():
    if is_pg():
        from db.pg_pool import pg_conn
        log.debug("init_topo_db: initialising topology tables in PostgreSQL")
        try:
            from db.pg_schema import pg_create_main_schema
            with pg_conn('main') as con:
                cur = con.cursor()
                # pg_create_main_schema creates topo tables via IF NOT EXISTS
                pg_create_main_schema(cur)
                cur.close()
            log.debug("init_topo_db: done")
        except Exception as e:
            log.error(f"init_topo_db PG error: {e}")
            raise
        return

    log.debug("init_topo_db: opening connection to %s", _DB_PATH)
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        log.debug("init_topo_db: creating tables")
        con.execute("""CREATE TABLE IF NOT EXISTS topo_pages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS topo_nodes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            type TEXT NOT NULL,
            x REAL DEFAULT 200,
            y REAL DEFAULT 200,
            properties TEXT DEFAULT '{}',
            page_id INTEGER DEFAULT 1
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS topo_links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id INTEGER NOT NULL,
            target_id INTEGER NOT NULL,
            label TEXT DEFAULT '',
            link_type TEXT DEFAULT 'trunk',
            page_id INTEGER DEFAULT 1
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS topo_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            color TEXT DEFAULT '#00d4ff',
            x REAL,
            y REAL,
            w REAL,
            h REAL,
            page_id INTEGER DEFAULT 1
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS topo_settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )""")
        con.commit()
        log.debug("init_topo_db: tables created/verified")
        # Migrations: add page_id to existing tables
        for stmt in [
            "ALTER TABLE topo_nodes  ADD COLUMN page_id INTEGER DEFAULT 1",
            "ALTER TABLE topo_links  ADD COLUMN page_id INTEGER DEFAULT 1",
            "ALTER TABLE topo_groups ADD COLUMN page_id INTEGER DEFAULT 1",
        ]:
            try:
                con.execute(stmt)
                con.commit()
                log.debug("init_topo_db: migration applied: %s", stmt)
            except Exception:
                pass  # column already exists
        # Seed default page if none exist
        if not con.execute("SELECT 1 FROM topo_pages").fetchone():
            con.execute("INSERT INTO topo_pages (id, name) VALUES (1, 'Main')")
            con.commit()
            log.debug("init_topo_db: seeded default page")
        log.debug("init_topo_db: done")
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


# ── Pages ──────────────────────────────────────────────────────────────────

def topo_get_pages():
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute('SELECT id, name FROM topo_pages ORDER BY id')
            return [dict(r) for r in cur.fetchall()]
    with _conn() as con:
        rows = con.execute('SELECT id, name FROM topo_pages ORDER BY id').fetchall()
        return [dict(r) for r in rows]


def topo_insert_page(name):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute('INSERT INTO topo_pages (name) VALUES (%s) RETURNING id', (name,))
            new_id = cur.fetchone()['id']
        return {'id': new_id, 'name': name}
    with _conn() as con:
        cur = con.execute('INSERT INTO topo_pages (name) VALUES (?)', (name,))
        return {'id': cur.lastrowid, 'name': name}


def topo_update_page(id_, name):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute('SELECT id FROM topo_pages WHERE id=%s', (id_,))
            if not cur.fetchone():
                return None
            cur.execute('UPDATE topo_pages SET name=%s WHERE id=%s', (name, id_))
        return {'id': id_, 'name': name}
    with _conn() as con:
        row = con.execute('SELECT id FROM topo_pages WHERE id=?', (id_,)).fetchone()
        if not row:
            return None
        con.execute('UPDATE topo_pages SET name=? WHERE id=?', (name, id_))
        return {'id': id_, 'name': name}


def topo_delete_page(id_):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute('DELETE FROM topo_nodes  WHERE page_id=%s', (id_,))
            cur.execute('DELETE FROM topo_links  WHERE page_id=%s', (id_,))
            cur.execute('DELETE FROM topo_groups WHERE page_id=%s', (id_,))
            cur.execute('DELETE FROM topo_pages  WHERE id=%s', (id_,))
        return
    with _conn() as con:
        con.execute('DELETE FROM topo_nodes  WHERE page_id=?', (id_,))
        con.execute('DELETE FROM topo_links  WHERE page_id=?', (id_,))
        con.execute('DELETE FROM topo_groups WHERE page_id=?', (id_,))
        con.execute('DELETE FROM topo_pages  WHERE id=?', (id_,))


# ── Nodes ──────────────────────────────────────────────────────────────────

def topo_get_nodes(page_id=None):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            if page_id is not None:
                cur.execute('SELECT * FROM topo_nodes WHERE page_id=%s ORDER BY id', (page_id,))
            else:
                cur.execute('SELECT * FROM topo_nodes ORDER BY id')
            return [_parse_node(r) for r in cur.fetchall()]
    with _conn() as con:
        if page_id is not None:
            rows = con.execute('SELECT * FROM topo_nodes WHERE page_id=? ORDER BY id', (page_id,)).fetchall()
        else:
            rows = con.execute('SELECT * FROM topo_nodes ORDER BY id').fetchall()
        return [_parse_node(r) for r in rows]


def topo_insert_node(name, type_, x=200, y=200, properties=None, page_id=1):
    props = json.dumps(properties or {})
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute(
                'INSERT INTO topo_nodes (name, type, x, y, properties, page_id) '
                'VALUES (%s,%s,%s,%s,%s,%s) RETURNING id',
                (name, type_, x, y, props, page_id)
            )
            new_id = cur.fetchone()['id']
        return {'id': new_id, 'name': name, 'type': type_, 'x': x, 'y': y,
                'properties': properties or {}, 'page_id': page_id}
    with _conn() as con:
        cur = con.execute(
            'INSERT INTO topo_nodes (name, type, x, y, properties, page_id) VALUES (?, ?, ?, ?, ?, ?)',
            (name, type_, x, y, props, page_id)
        )
        return {'id': cur.lastrowid, 'name': name, 'type': type_, 'x': x, 'y': y,
                'properties': properties or {}, 'page_id': page_id}


def topo_update_node(id_, name=None, type_=None, x=None, y=None, properties=None):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute('SELECT * FROM topo_nodes WHERE id=%s', (id_,))
            row = cur.fetchone()
            if not row:
                return None
            updated = {
                'name': name if name is not None else row['name'],
                'type': type_ if type_ is not None else row['type'],
                'x': x if x is not None else row['x'],
                'y': y if y is not None else row['y'],
                'properties': json.dumps(properties if properties is not None
                                         else json.loads(row['properties'] or '{}')),
            }
            cur.execute(
                'UPDATE topo_nodes SET name=%s, type=%s, x=%s, y=%s, properties=%s WHERE id=%s',
                (updated['name'], updated['type'], updated['x'], updated['y'],
                 updated['properties'], id_)
            )
        return {'id': id_, **updated, 'properties': json.loads(updated['properties'])}
    with _conn() as con:
        row = con.execute('SELECT * FROM topo_nodes WHERE id=?', (id_,)).fetchone()
        if not row:
            return None
        updated = {
            'name': name if name is not None else row['name'],
            'type': type_ if type_ is not None else row['type'],
            'x': x if x is not None else row['x'],
            'y': y if y is not None else row['y'],
            'properties': json.dumps(properties if properties is not None else json.loads(row['properties'] or '{}')),
        }
        con.execute(
            'UPDATE topo_nodes SET name=?, type=?, x=?, y=?, properties=? WHERE id=?',
            (updated['name'], updated['type'], updated['x'], updated['y'], updated['properties'], id_)
        )
        return {'id': id_, **updated, 'properties': json.loads(updated['properties'])}


def topo_delete_node(id_):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute('DELETE FROM topo_links WHERE source_id=%s OR target_id=%s', (id_, id_))
            cur.execute('DELETE FROM topo_nodes WHERE id=%s', (id_,))
        return
    with _conn() as con:
        con.execute('DELETE FROM topo_links WHERE source_id=? OR target_id=?', (id_, id_))
        con.execute('DELETE FROM topo_nodes WHERE id=?', (id_,))


# ── Links ──────────────────────────────────────────────────────────────────

def topo_get_links(page_id=None):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            if page_id is not None:
                cur.execute('SELECT * FROM topo_links WHERE page_id=%s ORDER BY id', (page_id,))
            else:
                cur.execute('SELECT * FROM topo_links ORDER BY id')
            return [dict(r) for r in cur.fetchall()]
    with _conn() as con:
        if page_id is not None:
            rows = con.execute('SELECT * FROM topo_links WHERE page_id=? ORDER BY id', (page_id,)).fetchall()
        else:
            rows = con.execute('SELECT * FROM topo_links ORDER BY id').fetchall()
        return [dict(r) for r in rows]


def topo_insert_link(source_id, target_id, label='', link_type='trunk', page_id=1):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute(
                'INSERT INTO topo_links (source_id, target_id, label, link_type, page_id) '
                'VALUES (%s,%s,%s,%s,%s) RETURNING id',
                (source_id, target_id, label, link_type, page_id)
            )
            new_id = cur.fetchone()['id']
        return {'id': new_id, 'source_id': source_id, 'target_id': target_id,
                'label': label, 'link_type': link_type, 'page_id': page_id}
    with _conn() as con:
        cur = con.execute(
            'INSERT INTO topo_links (source_id, target_id, label, link_type, page_id) VALUES (?, ?, ?, ?, ?)',
            (source_id, target_id, label, link_type, page_id)
        )
        return {'id': cur.lastrowid, 'source_id': source_id, 'target_id': target_id,
                'label': label, 'link_type': link_type, 'page_id': page_id}


def topo_update_link(id_, label='', link_type='trunk', source_id=None, target_id=None):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute('SELECT * FROM topo_links WHERE id=%s', (id_,))
            row = cur.fetchone()
            if not row:
                return None
            lbl = label if label is not None else row['label']
            lt  = link_type if link_type is not None else row['link_type']
            src = source_id if source_id is not None else row['source_id']
            tgt = target_id if target_id is not None else row['target_id']
            cur.execute(
                'UPDATE topo_links SET label=%s, link_type=%s, source_id=%s, target_id=%s WHERE id=%s',
                (lbl, lt, src, tgt, id_)
            )
        return {**dict(row), 'label': lbl, 'link_type': lt, 'source_id': src, 'target_id': tgt}
    with _conn() as con:
        row = con.execute('SELECT * FROM topo_links WHERE id=?', (id_,)).fetchone()
        if not row:
            return None
        lbl = label if label is not None else row['label']
        lt  = link_type if link_type is not None else row['link_type']
        src = source_id if source_id is not None else row['source_id']
        tgt = target_id if target_id is not None else row['target_id']
        con.execute(
            'UPDATE topo_links SET label=?, link_type=?, source_id=?, target_id=? WHERE id=?',
            (lbl, lt, src, tgt, id_)
        )
        return {**dict(row), 'label': lbl, 'link_type': lt, 'source_id': src, 'target_id': tgt}


def topo_delete_link(id_):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute('DELETE FROM topo_links WHERE id=%s', (id_,))
        return
    with _conn() as con:
        con.execute('DELETE FROM topo_links WHERE id=?', (id_,))


# ── Groups ─────────────────────────────────────────────────────────────────

def topo_get_groups(page_id=None):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            if page_id is not None:
                cur.execute('SELECT * FROM topo_groups WHERE page_id=%s ORDER BY id', (page_id,))
            else:
                cur.execute('SELECT * FROM topo_groups ORDER BY id')
            return [dict(r) for r in cur.fetchall()]
    with _conn() as con:
        if page_id is not None:
            rows = con.execute('SELECT * FROM topo_groups WHERE page_id=? ORDER BY id', (page_id,)).fetchall()
        else:
            rows = con.execute('SELECT * FROM topo_groups ORDER BY id').fetchall()
        return [dict(r) for r in rows]


def topo_insert_group(name, color='#00d4ff', x=100, y=100, w=300, h=200, page_id=1):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute(
                'INSERT INTO topo_groups (name, color, x, y, w, h, page_id) '
                'VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id',
                (name, color, x, y, w, h, page_id)
            )
            new_id = cur.fetchone()['id']
        return {'id': new_id, 'name': name, 'color': color,
                'x': x, 'y': y, 'w': w, 'h': h, 'page_id': page_id}
    with _conn() as con:
        cur = con.execute(
            'INSERT INTO topo_groups (name, color, x, y, w, h, page_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (name, color, x, y, w, h, page_id)
        )
        return {'id': cur.lastrowid, 'name': name, 'color': color,
                'x': x, 'y': y, 'w': w, 'h': h, 'page_id': page_id}


def topo_update_group(id_, name=None, color=None, x=None, y=None, w=None, h=None):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute('SELECT * FROM topo_groups WHERE id=%s', (id_,))
            row = cur.fetchone()
            if not row:
                return None
            n  = name  if name  is not None else row['name']
            c  = color if color is not None else row['color']
            gx = x     if x     is not None else row['x']
            gy = y     if y     is not None else row['y']
            gw = w     if w     is not None else row['w']
            gh = h     if h     is not None else row['h']
            cur.execute(
                'UPDATE topo_groups SET name=%s, color=%s, x=%s, y=%s, w=%s, h=%s WHERE id=%s',
                (n, c, gx, gy, gw, gh, id_)
            )
        return {'id': id_, 'name': n, 'color': c, 'x': gx, 'y': gy, 'w': gw, 'h': gh}
    with _conn() as con:
        row = con.execute('SELECT * FROM topo_groups WHERE id=?', (id_,)).fetchone()
        if not row:
            return None
        n = name if name is not None else row['name']
        c = color if color is not None else row['color']
        gx = x if x is not None else row['x']
        gy = y if y is not None else row['y']
        gw = w if w is not None else row['w']
        gh = h if h is not None else row['h']
        con.execute(
            'UPDATE topo_groups SET name=?, color=?, x=?, y=?, w=?, h=? WHERE id=?',
            (n, c, gx, gy, gw, gh, id_)
        )
        return {'id': id_, 'name': n, 'color': c, 'x': gx, 'y': gy, 'w': gw, 'h': gh}


def topo_delete_group(id_):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute('DELETE FROM topo_groups WHERE id=%s', (id_,))
        return
    with _conn() as con:
        con.execute('DELETE FROM topo_groups WHERE id=?', (id_,))


# ── Settings ───────────────────────────────────────────────────────────────

def topo_get_setting(key):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute('SELECT value FROM topo_settings WHERE key=%s', (key,))
            row = cur.fetchone()
        if not row:
            return None
        return {'key': key, 'value': json.loads(row['value'])}
    with _conn() as con:
        row = con.execute('SELECT value FROM topo_settings WHERE key=?', (key,)).fetchone()
        if not row:
            return None
        return {'key': key, 'value': json.loads(row['value'])}


def topo_upsert_setting(key, value_obj):
    if is_pg():
        from db.pg_pool import pg_cursor
        with pg_cursor('main') as cur:
            cur.execute(
                'INSERT INTO topo_settings (key, value) VALUES (%s,%s) '
                'ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value',
                (key, json.dumps(value_obj))
            )
        return
    with _conn() as con:
        con.execute(
            'INSERT OR REPLACE INTO topo_settings (key, value) VALUES (?, ?)',
            (key, json.dumps(value_obj))
        )


def topo_prune_pw_links(did):
    """Remove all pw_links entries that reference the given device_id (src or tgt).
    Called automatically when a device is deleted so stale links don't survive."""
    did_str = str(did)
    row = topo_get_setting('pw_links')
    if not row or not isinstance(row.get('value'), list):
        return
    pruned = [lk for lk in row['value']
              if str(lk.get('src_did', '')) != did_str
              and str(lk.get('tgt_did', '')) != did_str]
    if len(pruned) != len(row['value']):
        topo_upsert_setting('pw_links', pruned)


# ── Migration ──────────────────────────────────────────────────────────────

def migrate_topo_from_file():
    """One-time: copy rows from old topo.db into pingwatch.db if topo.db still exists."""
    _root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _old = os.path.join(_root, 'topo.db')
    if not os.path.exists(_old):
        return
    src = None
    try:
        src = sqlite3.connect(_old)
        with _conn() as dst:
            for tbl in ('topo_pages', 'topo_nodes', 'topo_links', 'topo_groups', 'topo_settings'):
                try:
                    rows = src.execute(f'SELECT * FROM {tbl}').fetchall()
                    if rows:
                        ncols = len(rows[0])
                        placeholders = ','.join('?' * ncols)
                        dst.executemany(
                            f'INSERT OR IGNORE INTO {tbl} VALUES ({placeholders})',
                            [tuple(r) for r in rows]
                        )
                        log.info(f'topo migration: copied {len(rows)} row(s) from {tbl}')
                except Exception as _e:
                    log.warning(f'topo migration: skipped {tbl} — {_e}')
        src.close()
        src = None
        os.rename(_old, _old + '.migrated')
        log.info('topo.db migrated into pingwatch.db — renamed to topo.db.migrated')
    except Exception as e:
        log.error(f'topo migration error: {e}')
    finally:
        if src:
            try: src.close()
            except Exception: pass


# ── Helpers ────────────────────────────────────────────────────────────────

def _parse_node(row):
    d = dict(row)
    d['properties'] = json.loads(d.get('properties') or '{}')
    return d
