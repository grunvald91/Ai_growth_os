"""Regression tests for room photos in the Telegram owner flow."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.handlers import admin
from bot.states import EmployeeEditStates


@pytest.fixture(autouse=True)
def reset_photo_sessions():
    admin._room_photo_capture_sessions.clear()


def _session(monkeypatch, employee, *, owner=True):
    import database.crud as crud

    session = MagicMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: employee))
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(admin, "async_session_maker", lambda: context)
    monkeypatch.setattr(crud, "get_employee_by_id", AsyncMock(return_value=employee))
    monkeypatch.setattr(admin, "get_user_by_telegram_id", AsyncMock(return_value=SimpleNamespace(id=71)))
    monkeypatch.setattr(admin, "is_business_owner", AsyncMock(return_value=owner))
    return session


def _callback(data="edit_emp_photos:39"):
    callback = SimpleNamespace(data=data, from_user=SimpleNamespace(id=437862772))
    callback.message = SimpleNamespace(edit_text=AsyncMock(), chat=SimpleNamespace(id=10))
    callback.answer = AsyncMock()
    return callback


def test_open_room_photo_menu_accepts_next_photo_without_extra_button(monkeypatch):
    employee = SimpleNamespace(id=39, business_id=11, name="Test room", photos=None)
    _session(monkeypatch, employee)
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
    callback = _callback()

    asyncio.run(admin.edit_emp_photos_menu(callback, state))

    state.update_data.assert_awaited_once_with(editing_employee_id=39)
    state.set_state.assert_awaited_once_with(EmployeeEditStates.adding_photo)
    callback.message.edit_text.assert_awaited_once()


def test_add_photo_button_prompts_instead_of_name_error(monkeypatch):
    employee = SimpleNamespace(id=39, business_id=11, name="Test room", photos=None)
    _session(monkeypatch, employee)
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
    callback = _callback("emp_add_photo:39")

    asyncio.run(admin.emp_add_photo_start(callback, state))

    callback.message.edit_text.assert_awaited_once()
    state.set_state.assert_awaited_once_with(EmployeeEditStates.adding_photo)


def test_add_photo_button_rejects_non_owner(monkeypatch):
    employee = SimpleNamespace(id=39, business_id=11, name="Other owner's room", photos=None)
    _session(monkeypatch, employee, owner=False)
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
    callback = _callback("emp_add_photo:39")

    asyncio.run(admin.emp_add_photo_start(callback, state))

    callback.message.edit_text.assert_not_awaited()
    state.set_state.assert_not_awaited()
    callback.answer.assert_awaited_once()


def test_done_exits_room_photo_capture(monkeypatch):
    edit_employee = AsyncMock()
    monkeypatch.setattr(admin, "edit_employee_menu", edit_employee)
    monkeypatch.setattr(admin, "edit_emp_photos_menu", AsyncMock())
    callback = _callback("emp_photos_done:39")
    state = SimpleNamespace(clear=AsyncMock())

    asyncio.run(admin.emp_photos_done(callback, state))

    state.clear.assert_awaited_once()
    edit_employee.assert_awaited_once_with(callback)


def test_back_exits_room_photo_capture(monkeypatch):
    edit_employee = AsyncMock()
    monkeypatch.setattr(admin, "edit_employee_menu", edit_employee)
    callback = _callback("emp_photos_back:39")
    state = SimpleNamespace(clear=AsyncMock())

    asyncio.run(admin.emp_photos_back(callback, state))

    state.clear.assert_awaited_once()
    edit_employee.assert_awaited_once_with(callback)


def test_room_photo_menu_rejects_non_owner(monkeypatch):
    employee = SimpleNamespace(id=39, business_id=11, name="Other owner's room", photos=None)
    _session(monkeypatch, employee, owner=False)
    state = SimpleNamespace(update_data=AsyncMock(), set_state=AsyncMock())
    callback = _callback()

    asyncio.run(admin.edit_emp_photos_menu(callback, state))

    callback.message.edit_text.assert_not_awaited()
    state.set_state.assert_not_awaited()
    callback.answer.assert_awaited_once()


def test_room_photo_receive_rejects_non_owner_without_database_write(monkeypatch):
    employee = SimpleNamespace(id=39, business_id=11, name="Other owner's room", photos=None)
    session = _session(monkeypatch, employee, owner=False)
    monkeypatch.setattr(admin, "_save_photo_to_cache", AsyncMock(return_value=True))
    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id="fake-test-file-id")],
        from_user=SimpleNamespace(id=437862772),
        bot=MagicMock(),
        answer=AsyncMock(),
        chat=SimpleNamespace(id=10),
    )
    state = SimpleNamespace(get_data=AsyncMock(return_value={"editing_employee_id": 39}), clear=AsyncMock())

    asyncio.run(admin.emp_add_photo_receive(message, state))

    session.commit.assert_not_awaited()
    assert employee.photos is None
    state.clear.assert_awaited_once()
    message.answer.assert_awaited_once()


def test_room_photo_receive_saves_authorized_photo_and_acknowledges(monkeypatch):
    employee = SimpleNamespace(id=39, business_id=11, name="Test room", photos=None)
    session = _session(monkeypatch, employee)
    cache = AsyncMock(return_value=True)
    monkeypatch.setattr(admin, "_save_photo_to_cache", cache)
    message = SimpleNamespace(
        photo=[SimpleNamespace(file_id="fake-test-file-id")],
        from_user=SimpleNamespace(id=437862772),
        bot=MagicMock(),
        answer=AsyncMock(),
        chat=SimpleNamespace(id=10),
    )
    state = SimpleNamespace(get_data=AsyncMock(return_value={"editing_employee_id": 39}))

    asyncio.run(admin.emp_add_photo_receive(message, state))

    session.commit.assert_awaited_once()
    assert employee.photos == '["fake-test-file-id"]'
    cache.assert_awaited_once_with(message.bot, "fake-test-file-id")
    message.answer.assert_awaited_once()


def test_parallel_photo_updates_do_not_overwrite_each_other(monkeypatch):
    import database.crud as crud

    async def exercise():
        stored = {"photos": None}
        both_read = asyncio.Event()
        reads = 0
        row_lock = asyncio.Lock()

        class Session:
            def __init__(self):
                self.employee = None
                self.locked = False

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                if self.locked:
                    row_lock.release()

            async def execute(self, query):
                from sqlalchemy.dialects import postgresql
                assert "FOR UPDATE" in str(query.compile(dialect=postgresql.dialect()))
                await row_lock.acquire()
                self.locked = True
                self.employee = SimpleNamespace(id=39, business_id=11, photos=stored["photos"])
                return SimpleNamespace(scalar_one_or_none=lambda: self.employee)

            async def commit(self):
                stored["photos"] = self.employee.photos
                if self.locked:
                    self.locked = False
                    row_lock.release()

        async def get_employee(session, employee_id):
            nonlocal reads
            session.employee = SimpleNamespace(id=employee_id, business_id=11, photos=stored["photos"])
            reads += 1
            if reads == 2:
                both_read.set()
            await both_read.wait()
            return session.employee

        monkeypatch.setattr(admin, "async_session_maker", Session)
        monkeypatch.setattr(crud, "get_employee_by_id", get_employee)
        monkeypatch.setattr(admin, "get_user_by_telegram_id", AsyncMock(return_value=SimpleNamespace(id=71)))
        monkeypatch.setattr(admin, "is_business_owner", AsyncMock(return_value=True))
        monkeypatch.setattr(admin, "_save_photo_to_cache", AsyncMock(return_value=True))
        state = SimpleNamespace(get_data=AsyncMock(return_value={"editing_employee_id": 39}))
        messages = [
            SimpleNamespace(photo=[SimpleNamespace(file_id=f"test-photo-{i}")],
                            from_user=SimpleNamespace(id=437862772), bot=MagicMock(), answer=AsyncMock(),
                            chat=SimpleNamespace(id=10), media_group_id=None)
            for i in (1, 2)
        ]
        await asyncio.gather(*(admin.emp_add_photo_receive(m, state) for m in messages))
        return json.loads(stored["photos"])

    assert sorted(asyncio.run(exercise())) == ["test-photo-1", "test-photo-2"]


def test_album_sends_one_summary_with_one_done_button(monkeypatch):
    employee = SimpleNamespace(id=39, business_id=11, name="Test room", photos=None)
    _session(monkeypatch, employee)
    monkeypatch.setattr(admin, "_save_photo_to_cache", AsyncMock(return_value=True))
    monkeypatch.setattr(admin, "_ROOM_PHOTO_ALBUM_SETTLE_SECONDS", 0.02, raising=False)

    async def exercise():
        state = SimpleNamespace(get_data=AsyncMock(return_value={"editing_employee_id": 39}))
        messages = [
            SimpleNamespace(photo=[SimpleNamespace(file_id=f"album-file-{i}")],
                            from_user=SimpleNamespace(id=437862772), bot=MagicMock(), answer=AsyncMock(),
                            chat=SimpleNamespace(id=10), media_group_id="album-123")
            for i in (1, 2)
        ]
        for message in messages:
            await admin.emp_add_photo_receive(message, state)
        await asyncio.sleep(0.08)
        return messages

    messages = asyncio.run(exercise())
    replies = [call for message in messages for call in message.answer.await_args_list]
    assert len(replies) == 1
    assert "2 фото" in replies[0].args[0]
    assert replies[0].kwargs["reply_markup"].inline_keyboard[0][0].text == "✅ Готово"
    assert len(json.loads(employee.photos)) == 2


def test_done_cancels_pending_album_summary(monkeypatch):
    employee = SimpleNamespace(id=39, business_id=11, name="Test room", photos=None)
    _session(monkeypatch, employee)
    monkeypatch.setattr(admin, "_save_photo_to_cache", AsyncMock(return_value=True))
    monkeypatch.setattr(admin, "_ROOM_PHOTO_ALBUM_SETTLE_SECONDS", 0.03)
    monkeypatch.setattr(admin, "edit_employee_menu", AsyncMock())

    async def exercise():
        state = SimpleNamespace(get_data=AsyncMock(return_value={"editing_employee_id": 39}), clear=AsyncMock())
        message = SimpleNamespace(photo=[SimpleNamespace(file_id="test-album-file")],
                                  from_user=SimpleNamespace(id=437862772), bot=MagicMock(), answer=AsyncMock(),
                                  chat=SimpleNamespace(id=10), media_group_id="album-done")
        await admin.emp_add_photo_receive(message, state)
        await admin.emp_photos_done(_callback("emp_photos_done:39"), state)
        await asyncio.sleep(0.08)
        return message

    message = asyncio.run(exercise())
    message.answer.assert_not_awaited()


def test_slow_cache_does_not_split_one_album_into_two_notifications(monkeypatch):
    employee = SimpleNamespace(id=39, business_id=11, name="Test room", photos=None)
    _session(monkeypatch, employee)
    monkeypatch.setattr(admin, "_ROOM_PHOTO_ALBUM_SETTLE_SECONDS", 0.02)

    async def cache(_bot, file_id):
        if file_id == "slow-photo":
            await asyncio.sleep(0.06)
        return True

    monkeypatch.setattr(admin, "_save_photo_to_cache", cache)

    async def exercise():
        state = SimpleNamespace(get_data=AsyncMock(return_value={"editing_employee_id": 39}))
        messages = [
            SimpleNamespace(photo=[SimpleNamespace(file_id=file_id)],
                            from_user=SimpleNamespace(id=437862772), bot=MagicMock(), answer=AsyncMock(),
                            chat=SimpleNamespace(id=10), media_group_id="slow-album")
            for file_id in ("slow-photo", "fast-photo")
        ]
        await asyncio.gather(*(admin.emp_add_photo_receive(m, state) for m in messages))
        await asyncio.sleep(0.06)
        return messages

    messages = asyncio.run(exercise())
    assert sum(m.answer.await_count for m in messages) == 1


@pytest.mark.parametrize("reopen", [False, True])
def test_done_while_photo_waits_for_db_lock_has_no_late_notification(monkeypatch, reopen):
    employee = SimpleNamespace(id=39, business_id=11, name="Test room", photos=None)
    session = _session(monkeypatch, employee)
    monkeypatch.setattr(admin, "_save_photo_to_cache", AsyncMock(return_value=True))
    monkeypatch.setattr(admin, "_ROOM_PHOTO_ALBUM_SETTLE_SECONDS", 0.02)
    monkeypatch.setattr(admin, "edit_employee_menu", AsyncMock())

    async def exercise():
        entered = asyncio.Event()
        release = asyncio.Event()
        original_execute = session.execute

        async def locked_execute(query):
            entered.set()
            await release.wait()
            return await original_execute(query)

        session.execute = locked_execute
        state = SimpleNamespace(get_data=AsyncMock(return_value={"editing_employee_id": 39}), clear=AsyncMock())
        message = SimpleNamespace(photo=[SimpleNamespace(file_id="late-photo")],
                                  from_user=SimpleNamespace(id=437862772), bot=MagicMock(), answer=AsyncMock(),
                                  chat=SimpleNamespace(id=10), media_group_id="late-album")
        receiving = asyncio.create_task(admin.emp_add_photo_receive(message, state))
        await entered.wait()
        await admin.emp_photos_done(_callback("emp_photos_done:39"), state)
        if reopen:
            admin._advance_room_photo_capture(10, 437862772, 39, True)
        release.set()
        await receiving
        await asyncio.sleep(0.06)
        return message

    message = asyncio.run(exercise())
    session.commit.assert_awaited_once()
    message.answer.assert_not_awaited()
