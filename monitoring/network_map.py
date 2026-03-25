import sqlite3
import json
import os

from core.config import DB_PATH as _DB_PATH   # topology tables now live in pingwatch.db
from core.logger import log


def _conn():
    con = sqlite3.connect(_DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_topo_db():
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
    with _conn() as con:
        rows = con.execute('SELECT id, name FROM topo_pages ORDER BY id').fetchall()
        return [dict(r) for r in rows]


def topo_insert_page(name):
    with _conn() as con:
        cur = con.execute('INSERT INTO topo_pages (name) VALUES (?)', (name,))
        return {'id': cur.lastrowid, 'name': name}


def topo_update_page(id_, name):
    with _conn() as con:
        row = con.execute('SELECT id FROM topo_pages WHERE id=?', (id_,)).fetchone()
        if not row:
            return None
        con.execute('UPDATE topo_pages SET name=? WHERE id=?', (name, id_))
        return {'id': id_, 'name': name}


def topo_delete_page(id_):
    with _conn() as con:
        con.execute('DELETE FROM topo_nodes  WHERE page_id=?', (id_,))
        con.execute('DELETE FROM topo_links  WHERE page_id=?', (id_,))
        con.execute('DELETE FROM topo_groups WHERE page_id=?', (id_,))
        con.execute('DELETE FROM topo_pages  WHERE id=?', (id_,))


# ── Nodes ──────────────────────────────────────────────────────────────────

def topo_get_nodes(page_id=None):
    with _conn() as con:
        if page_id is not None:
            rows = con.execute('SELECT * FROM topo_nodes WHERE page_id=? ORDER BY id', (page_id,)).fetchall()
        else:
            rows = con.execute('SELECT * FROM topo_nodes ORDER BY id').fetchall()
        return [_parse_node(r) for r in rows]


def topo_insert_node(name, type_, x=200, y=200, properties=None, page_id=1):
    props = json.dumps(properties or {})
    with _conn() as con:
        cur = con.execute(
            'INSERT INTO topo_nodes (name, type, x, y, properties, page_id) VALUES (?, ?, ?, ?, ?, ?)',
            (name, type_, x, y, props, page_id)
        )
        return {'id': cur.lastrowid, 'name': name, 'type': type_, 'x': x, 'y': y, 'properties': properties or {}, 'page_id': page_id}


def topo_update_node(id_, name=None, type_=None, x=None, y=None, properties=None):
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
    with _conn() as con:
        con.execute('DELETE FROM topo_links WHERE source_id=? OR target_id=?', (id_, id_))
        con.execute('DELETE FROM topo_nodes WHERE id=?', (id_,))


# ── Links ──────────────────────────────────────────────────────────────────

def topo_get_links(page_id=None):
    with _conn() as con:
        if page_id is not None:
            rows = con.execute('SELECT * FROM topo_links WHERE page_id=? ORDER BY id', (page_id,)).fetchall()
        else:
            rows = con.execute('SELECT * FROM topo_links ORDER BY id').fetchall()
        return [dict(r) for r in rows]


def topo_insert_link(source_id, target_id, label='', link_type='trunk', page_id=1):
    with _conn() as con:
        cur = con.execute(
            'INSERT INTO topo_links (source_id, target_id, label, link_type, page_id) VALUES (?, ?, ?, ?, ?)',
            (source_id, target_id, label, link_type, page_id)
        )
        return {'id': cur.lastrowid, 'source_id': source_id, 'target_id': target_id, 'label': label, 'link_type': link_type, 'page_id': page_id}


def topo_update_link(id_, label='', link_type='trunk', source_id=None, target_id=None):
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
    with _conn() as con:
        con.execute('DELETE FROM topo_links WHERE id=?', (id_,))


# ── Groups ─────────────────────────────────────────────────────────────────

def topo_get_groups(page_id=None):
    with _conn() as con:
        if page_id is not None:
            rows = con.execute('SELECT * FROM topo_groups WHERE page_id=? ORDER BY id', (page_id,)).fetchall()
        else:
            rows = con.execute('SELECT * FROM topo_groups ORDER BY id').fetchall()
        return [dict(r) for r in rows]


def topo_insert_group(name, color='#00d4ff', x=100, y=100, w=300, h=200, page_id=1):
    with _conn() as con:
        cur = con.execute(
            'INSERT INTO topo_groups (name, color, x, y, w, h, page_id) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (name, color, x, y, w, h, page_id)
        )
        return {'id': cur.lastrowid, 'name': name, 'color': color, 'x': x, 'y': y, 'w': w, 'h': h, 'page_id': page_id}


def topo_update_group(id_, name=None, color=None, x=None, y=None, w=None, h=None):
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
    with _conn() as con:
        con.execute('DELETE FROM topo_groups WHERE id=?', (id_,))


# ── Settings ───────────────────────────────────────────────────────────────

def topo_get_setting(key):
    with _conn() as con:
        row = con.execute('SELECT value FROM topo_settings WHERE key=?', (key,)).fetchone()
        if not row:
            return None
        return {'key': key, 'value': json.loads(row['value'])}


def topo_upsert_setting(key, value_obj):
    with _conn() as con:
        con.execute(
            'INSERT OR REPLACE INTO topo_settings (key, value) VALUES (?, ?)',
            (key, json.dumps(value_obj))
        )


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
