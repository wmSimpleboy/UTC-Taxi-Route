from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from datetime import datetime, timedelta

from db import get_month_stats, get_report_by_filter, get_stats_in_range, get_today_stats
from telegram_notify import get_superuser_chat_ids, get_user_chat_ids


log = logging.getLogger(__name__)


def _format_sum(value: float) -> str:
    return f"{value:,.0f}".replace(",", " ")


# ---------------------------------------------------------------------------
# Month name parsing (Russian / Uzbek latin / Uzbek cyrillic)
# ---------------------------------------------------------------------------

_MONTH_NAMES: dict[str, int] = {}

_RU_MONTHS = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]
_UZ_LATIN_MONTHS = [
    "yanvar", "fevral", "mart", "aprel", "may", "iyun",
    "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr",
]
_UZ_CYRILLIC_MONTHS = [
    "январ", "феврал", "март", "апрел", "май", "июн",
    "июл", "август", "сентабр", "октабр", "ноябр", "декабр",
]

for _month_num, (_ru, _uz_lat, _uz_cyr) in enumerate(
    zip(_RU_MONTHS, _UZ_LATIN_MONTHS, _UZ_CYRILLIC_MONTHS), 1
):
    _MONTH_NAMES[_ru] = _month_num
    _MONTH_NAMES[_uz_lat] = _month_num
    _MONTH_NAMES[_uz_cyr] = _month_num


def _parse_month_name(text: str) -> int | None:
    t = text.strip().lower()
    if not t or len(t) < 3:
        return None
    if t in _MONTH_NAMES:
        return _MONTH_NAMES[t]
    for name, num in _MONTH_NAMES.items():
        if name.startswith(t) or t.startswith(name):
            return num
    return None


def _parse_date_arg(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d")


def _month_range(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1)
    if month == 12:
        end = datetime(year + 1, 1, 1)
    else:
        end = datetime(year, month + 1, 1)
    return start, end


def _format_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _build_report_text(title: str, start: datetime, end_exclusive: datetime) -> str:
    filters = get_report_by_filter(start, end_exclusive)
    if not filters:
        return f"{title}\n\nНет подтверждённых поездок за этот период."

    lines = [title, ""]
    grand_total = 0.0
    for fs in filters:
        lines.append(
            f"{fs.filter_name}: {fs.trips_count} поездок — {_format_sum(fs.total_cost)} сум"
        )
        grand_total += fs.total_cost

    lines.append("")
    lines.append(f"Итого: {_format_sum(grand_total)} сум")
    return "\n".join(lines)


def _try_parse_date_input(text: str) -> tuple[str, datetime, datetime] | None:
    """Parse user text as a date, date range, or month name.
    Returns (title, start, end_exclusive) or None."""
    text = text.strip()
    now = datetime.now()

    # Single date: YYYY-MM-DD
    m = re.match(r"^(\d{4}-\d{2}-\d{2})$", text)
    if m:
        try:
            dt = _parse_date_arg(m.group(1))
            return (
                f"Отчёт за {_format_date(dt)}",
                dt.replace(hour=0, minute=0, second=0),
                dt.replace(hour=0, minute=0, second=0) + timedelta(days=1),
            )
        except ValueError:
            pass

    # Date range: YYYY-MM-DD YYYY-MM-DD
    m = re.match(r"^(\d{4}-\d{2}-\d{2})\s+(\d{4}-\d{2}-\d{2})$", text)
    if m:
        try:
            d1 = _parse_date_arg(m.group(1))
            d2 = _parse_date_arg(m.group(2))
            start = min(d1, d2)
            end = max(d1, d2)
            return (
                f"Отчёт за {_format_date(start)} — {_format_date(end)}",
                start,
                end + timedelta(days=1),
            )
        except ValueError:
            pass

    # Month name
    month_num = _parse_month_name(text)
    if month_num is not None:
        year = now.year
        if month_num > now.month:
            year -= 1
        start, end_excl = _month_range(year, month_num)
        return f"Отчёт за {text.capitalize()} {year}", start, end_excl

    return None


def start_telegram_bot_thread() -> threading.Thread | None:
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        log.info("Telegram bot not started: TELEGRAM_BOT_TOKEN is empty.")
        return None

    def _run() -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(_run_bot(token))
        except Exception:
            log.exception("Telegram bot thread crashed")

    async def _run_bot(bot_token: str) -> None:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
        from telegram.ext import (
            ApplicationBuilder,
            CallbackQueryHandler,
            CommandHandler,
            ContextTypes,
            MessageHandler,
            filters,
        )

        user_ids = set(get_user_chat_ids())
        superuser_ids = set(get_superuser_chat_ids())
        all_ids = user_ids | superuser_ids

        log.info("Telegram bot: user_ids=%s, superuser_ids=%s", user_ids, superuser_ids)

        if not all_ids:
            log.warning("Telegram bot: no chat IDs configured.")

        def _is_allowed(chat_id: int) -> bool:
            return chat_id in all_ids

        def _is_superuser(chat_id: int) -> bool:
            return chat_id in superuser_ids

        # --- /today ---
        async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.effective_chat or not _is_allowed(update.effective_chat.id):
                return
            stats = get_today_stats()
            await update.message.reply_text(  # type: ignore[union-attr]
                f"Сегодня: {stats.trips_count} подтвержд. поездок\n"
                f"Итого: {stats.total_km:.1f} км, {_format_sum(stats.total_cost)} сум"
            )

        # --- /month ---
        async def cmd_month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.effective_chat or not _is_allowed(update.effective_chat.id):
                return
            stats = get_month_stats()
            await update.message.reply_text(  # type: ignore[union-attr]
                f"Этот месяц: {stats.trips_count} подтвержд. поездок\n"
                f"Итого: {stats.total_km:.1f} км, {_format_sum(stats.total_cost)} сум"
            )

        # --- /report YYYY-MM-DD YYYY-MM-DD ---
        async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.effective_chat or not _is_allowed(update.effective_chat.id):
                return
            args_list = list(context.args or [])
            if len(args_list) != 2:
                await update.message.reply_text(  # type: ignore[union-attr]
                    "Использование: /report YYYY-MM-DD YYYY-MM-DD"
                )
                return
            try:
                start = _parse_date_arg(args_list[0])
                end = _parse_date_arg(args_list[1])
            except ValueError:
                await update.message.reply_text(  # type: ignore[union-attr]
                    "Неверный формат дат. Пример: /report 2026-04-01 2026-04-02"
                )
                return
            end_exclusive = end + timedelta(days=1)
            stats = get_stats_in_range(start, end_exclusive)
            await update.message.reply_text(  # type: ignore[union-attr]
                f"От {_format_date(start)} до {_format_date(end)}: {stats.trips_count} поездок\n"
                f"Итого: {stats.total_km:.1f} км, {_format_sum(stats.total_cost)} сум"
            )

        # --- /otchet (superuser only) → inline buttons ---
        async def cmd_otchet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.effective_chat:
                return
            if not _is_superuser(update.effective_chat.id):
                if update.message:
                    await update.message.reply_text("Эта команда доступна только для суперпользователей.")
                return

            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("Сегодня", callback_data="otchet:today"),
                    InlineKeyboardButton("Этот месяц", callback_data="otchet:month"),
                ],
                [
                    InlineKeyboardButton("Указать дату", callback_data="otchet:custom"),
                ],
            ])
            await update.message.reply_text(  # type: ignore[union-attr]
                "Выберите период для отчёта:",
                reply_markup=keyboard,
            )

        # --- Callback handler for otchet buttons ---
        async def otchet_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            query = update.callback_query
            if not query or not update.effective_chat:
                return
            if not _is_superuser(update.effective_chat.id):
                await query.answer("Нет доступа.")
                return

            await query.answer()
            data = query.data or ""

            if data == "otchet:today":
                now = datetime.now()
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                end_excl = start + timedelta(days=1)
                title = f"Отчёт за {_format_date(now)}"
                msg = _build_report_text(title, start, end_excl)
                await query.edit_message_text(msg)

            elif data == "otchet:month":
                now = datetime.now()
                start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                if start.month == 12:
                    end_excl = start.replace(year=start.year + 1, month=1)
                else:
                    end_excl = start.replace(month=start.month + 1)
                title = f"Отчёт за текущий месяц ({_format_date(start)} — {_format_date(end_excl - timedelta(days=1))})"
                msg = _build_report_text(title, start, end_excl)
                await query.edit_message_text(msg)

            elif data == "otchet:custom":
                # Set flag so the next text message from this user is treated as date input.
                context.user_data["waiting_otchet_date"] = True  # type: ignore[index]
                await query.edit_message_text(
                    "Введите дату, диапазон дат или название месяца:\n\n"
                    "• 2026-04-01\n"
                    "• 2026-04-01 2026-04-15\n"
                    "• Апрель / Aprel / Апрел"
                )

        # --- Text message handler for custom date input ---
        async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
            if not update.effective_chat or not update.message or not update.message.text:
                return
            if not _is_superuser(update.effective_chat.id):
                return
            if not context.user_data.get("waiting_otchet_date"):  # type: ignore[union-attr]
                return

            # Clear the flag first.
            context.user_data["waiting_otchet_date"] = False  # type: ignore[index]

            parsed = _try_parse_date_input(update.message.text)
            if parsed is None:
                context.user_data["waiting_otchet_date"] = True  # type: ignore[index]
                await update.message.reply_text(
                    "Не удалось распознать дату. Попробуйте ещё раз:\n\n"
                    "• 2026-04-01\n"
                    "• 2026-04-01 2026-04-15\n"
                    "• Апрель / Aprel / Апрел"
                )
                return

            title, start, end_excl = parsed
            msg = _build_report_text(title, start, end_excl)
            await update.message.reply_text(msg)

        # --- Error handler ---
        async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
            log.error("Telegram handler error: %s", context.error, exc_info=context.error)

        application = ApplicationBuilder().token(bot_token).build()
        application.add_error_handler(error_handler)
        application.add_handler(CommandHandler("today", cmd_today))
        application.add_handler(CommandHandler("month", cmd_month))
        application.add_handler(CommandHandler("report", cmd_report))
        application.add_handler(CommandHandler("otchet", cmd_otchet))
        application.add_handler(CallbackQueryHandler(otchet_callback, pattern=r"^otchet:"))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

        log.info("Telegram bot polling started.")
        async with application:
            await application.start()
            await application.updater.start_polling()
            # Block until the thread is interrupted
            stop_event = asyncio.Event()
            await stop_event.wait()

    th = threading.Thread(target=_run, daemon=True, name="telegram-bot")
    th.start()
    return th
