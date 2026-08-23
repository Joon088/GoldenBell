import asyncpg


class Database:
    def __init__(self, database_url: str):
        self.database_url = database_url
        self.pool = None

    async def connect(self):
        self.pool = await asyncpg.create_pool(
            self.database_url,
            min_size=1,
            max_size=5,
            command_timeout=30,
        )
        await self.init()

    async def close(self):
        if self.pool:
            await self.pool.close()

    async def init(self):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    user_id BIGINT PRIMARY KEY,
                    account_number TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS tickets (
                    id BIGSERIAL PRIMARY KEY,
                    kind TEXT NOT NULL CHECK (kind IN ('pt', 'steroid')),
                    guild_id BIGINT NOT NULL,
                    channel_id BIGINT NOT NULL,
                    message_id BIGINT,
                    creator_id BIGINT NOT NULL,
                    target_count INTEGER NOT NULL CHECK (target_count > 0),
                    current_count INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'closed')),
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    closed_at TIMESTAMPTZ
                )
            """)

            await conn.execute("""
                CREATE TABLE IF NOT EXISTS ticket_entries (
                    id BIGSERIAL PRIMARY KEY,
                    ticket_id BIGINT NOT NULL REFERENCES tickets(id) ON DELETE CASCADE,
                    user_id BIGINT NOT NULL,
                    delta INTEGER NOT NULL CHECK (delta > 0),
                    label TEXT NOT NULL,
                    is_reverted BOOLEAN NOT NULL DEFAULT FALSE,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_ticket_entries_ticket
                ON ticket_entries(ticket_id)
            """)

            await conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tickets_status
                ON tickets(status)
            """)

    async def set_account(self, user_id: int, account_number: str):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                INSERT INTO accounts (user_id, account_number)
                VALUES ($1, $2)
                ON CONFLICT (user_id)
                DO UPDATE SET
                    account_number = EXCLUDED.account_number,
                    updated_at = NOW()
            """, user_id, account_number)

    async def get_account(self, user_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT account_number FROM accounts WHERE user_id = $1",
                user_id
            )
            return row["account_number"] if row else None

    async def create_ticket(self, kind, guild_id, channel_id, creator_id, target_count):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO tickets
                    (kind, guild_id, channel_id, creator_id, target_count)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING *
            """, kind, guild_id, channel_id, creator_id, target_count)
            return dict(row)

    async def set_ticket_message(self, ticket_id: int, message_id: int):
        async with self.pool.acquire() as conn:
            await conn.execute("""
                UPDATE tickets
                SET message_id = $2
                WHERE id = $1
            """, ticket_id, message_id)

    async def get_ticket(self, ticket_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM tickets WHERE id = $1",
                ticket_id
            )
            return dict(row) if row else None

    async def get_open_tickets(self):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM tickets
                WHERE status = 'open' AND message_id IS NOT NULL
                ORDER BY id ASC
            """)
            return [dict(r) for r in rows]

    async def add_entry(self, ticket_id: int, user_id: int, delta: int, label: str):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                ticket = await conn.fetchrow("""
                    SELECT * FROM tickets
                    WHERE id = $1
                    FOR UPDATE
                """, ticket_id)

                if not ticket or ticket["status"] != "open":
                    return None, "closed"

                remaining = ticket["target_count"] - ticket["current_count"]
                if delta > remaining:
                    return dict(ticket), "over"

                await conn.execute("""
                    INSERT INTO ticket_entries
                        (ticket_id, user_id, delta, label)
                    VALUES ($1, $2, $3, $4)
                """, ticket_id, user_id, delta, label)

                row = await conn.fetchrow("""
                    UPDATE tickets
                    SET current_count = current_count + $2
                    WHERE id = $1
                    RETURNING *
                """, ticket_id, delta)
                return dict(row), "ok"

    async def undo_last_entry(self, ticket_id: int, user_id: int):
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                ticket = await conn.fetchrow("""
                    SELECT * FROM tickets
                    WHERE id = $1
                    FOR UPDATE
                """, ticket_id)

                if not ticket or ticket["status"] != "open":
                    return None

                entry = await conn.fetchrow("""
                    SELECT * FROM ticket_entries
                    WHERE ticket_id = $1
                      AND user_id = $2
                      AND is_reverted = FALSE
                    ORDER BY id DESC
                    LIMIT 1
                    FOR UPDATE
                """, ticket_id, user_id)

                if not entry:
                    return None

                await conn.execute("""
                    UPDATE ticket_entries
                    SET is_reverted = TRUE
                    WHERE id = $1
                """, entry["id"])

                row = await conn.fetchrow("""
                    UPDATE tickets
                    SET current_count = GREATEST(current_count - $2, 0)
                    WHERE id = $1
                    RETURNING *
                """, ticket_id, entry["delta"])
                return dict(row)

    async def close_ticket(self, ticket_id: int):
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE tickets
                SET status = 'closed',
                    closed_at = NOW()
                WHERE id = $1 AND status = 'open'
                RETURNING *
            """, ticket_id)
            if row:
                return dict(row)
            return await self.get_ticket(ticket_id)

    async def get_ticket_totals_by_user(self, ticket_id: int):
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT user_id, COALESCE(SUM(delta), 0)::INTEGER AS count
                FROM ticket_entries
                WHERE ticket_id = $1
                  AND is_reverted = FALSE
                GROUP BY user_id
                HAVING COALESCE(SUM(delta), 0) > 0
                ORDER BY user_id
            """, ticket_id)
            return [dict(r) for r in rows]

    async def manual_add(self, ticket_id: int, user_id: int, count: int):
        """관리자가 기존 진행분을 특정 티켓/유저에게 수기로 추가."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                ticket = await conn.fetchrow("""
                    SELECT * FROM tickets
                    WHERE id = $1
                    FOR UPDATE
                """, ticket_id)

                if not ticket or ticket["status"] != "open":
                    return None, "closed"

                remaining = ticket["target_count"] - ticket["current_count"]
                if count > remaining:
                    return dict(ticket), "over"

                await conn.execute("""
                    INSERT INTO ticket_entries
                        (ticket_id, user_id, delta, label)
                    VALUES ($1, $2, $3, '관리자 수기등록')
                """, ticket_id, user_id, count)

                row = await conn.fetchrow("""
                    UPDATE tickets
                    SET current_count = current_count + $2
                    WHERE id = $1
                    RETURNING *
                """, ticket_id, count)
                return dict(row), "ok"

    async def manual_subtract(self, ticket_id: int, user_id: int, count: int):
        """특정 유저의 유효 기록에서 count만큼 차감. 여러 기록에 걸쳐 부분 차감 가능."""
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                ticket = await conn.fetchrow("""
                    SELECT * FROM tickets
                    WHERE id = $1
                    FOR UPDATE
                """, ticket_id)

                if not ticket or ticket["status"] != "open":
                    return None, "closed"

                rows = await conn.fetch("""
                    SELECT *
                    FROM ticket_entries
                    WHERE ticket_id = $1
                      AND user_id = $2
                      AND is_reverted = FALSE
                    ORDER BY id DESC
                    FOR UPDATE
                """, ticket_id, user_id)

                available = sum(r["delta"] for r in rows)
                if count > available:
                    return dict(ticket), "not_enough"

                left = count
                for entry in rows:
                    if left <= 0:
                        break
                    delta = entry["delta"]
                    if delta <= left:
                        await conn.execute("""
                            UPDATE ticket_entries
                            SET is_reverted = TRUE
                            WHERE id = $1
                        """, entry["id"])
                        left -= delta
                    else:
                        # 원래 기록을 취소하고, 남은 횟수를 새 보정 기록으로 남김.
                        await conn.execute("""
                            UPDATE ticket_entries
                            SET is_reverted = TRUE
                            WHERE id = $1
                        """, entry["id"])
                        remain_delta = delta - left
                        await conn.execute("""
                            INSERT INTO ticket_entries
                                (ticket_id, user_id, delta, label)
                            VALUES ($1, $2, $3, '수기차감 후 잔여')
                        """, ticket_id, user_id, remain_delta)
                        left = 0

                row = await conn.fetchrow("""
                    UPDATE tickets
                    SET current_count = GREATEST(current_count - $2, 0)
                    WHERE id = $1
                    RETURNING *
                """, ticket_id, count)
                return dict(row), "ok"

