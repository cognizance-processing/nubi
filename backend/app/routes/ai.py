import asyncio
import json
import time
from typing import Dict, Any, Optional, List, AsyncGenerator

import requests
from fastapi import APIRouter, HTTPException, Body, Depends, Request
from fastapi.responses import StreamingResponse

from ..db import get_pool
from ..auth import get_optional_user
from ..prompts import (
    BOARD_SYSTEM_INSTRUCTION, EXPLORATION_SYSTEM_INSTRUCTION,
    DATASTORE_SYSTEM_INSTRUCTION, GENERAL_SYSTEM_INSTRUCTION,
)
from ..helpers import strip_markdown_code_block, validate_html
from ..query_engine import (
    test_query as run_query, execute_query_direct,
    get_datastore_schema, get_bigquery_schema, get_sql_schema,
)
from ..ai_tools import (
    GEMINI_TOOLS, get_tools_for_context,
    get_available_datastores, get_available_boards,
    get_board_queries, get_query_code, get_board_code,
    search_board_code, search_query_code,
    create_or_update_query, delete_query,
    create_or_update_datastore, test_datastore_tool,
    save_keyfile_tool,
)
from ..llm import call_llm, LLMResponse, DEFAULT_MODEL, get_available_models, get_model_info

router = APIRouter(tags=["ai"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _log_usage(user_id: Optional[str], chat_id: Optional[str], resp: LLMResponse):
    if not user_id:
        return
    try:
        pool = get_pool()
        await pool.execute(
            "INSERT INTO ai_usage (user_id, chat_id, model, provider, input_tokens, output_tokens) "
            "VALUES ($1, $2, $3, $4, $5, $6)",
            user_id, chat_id, resp.model, resp.provider, resp.input_tokens, resp.output_tokens,
        )
    except Exception as e:
        print(f"WARNING: Failed to log usage: {e}")


def _chat_to_messages(chat: List[Dict[str, str]]) -> List[dict]:
    messages = []
    for msg in chat:
        if not msg.get("content"):
            continue
        role = "assistant" if msg.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": msg["content"]})
    return messages


def _get_system_instruction(context: str) -> str:
    if context == "board":
        return BOARD_SYSTEM_INSTRUCTION
    elif context == "query":
        return EXPLORATION_SYSTEM_INSTRUCTION
    elif context == "datastore":
        return DATASTORE_SYSTEM_INSTRUCTION
    else:
        return GENERAL_SYSTEM_INSTRUCTION


async def _execute_tool(func_name: str, func_args: dict, user_id: Optional[str] = None, org_id: Optional[str] = None) -> dict:
    if func_name == "list_datastores":
        ds = await get_available_datastores(user_id=user_id, org_id=org_id)
        return {"datastores": ds, "count": len(ds)}
    elif func_name == "list_boards":
        boards = await get_available_boards()
        return {"boards": boards, "count": len(boards)}
    elif func_name == "list_board_queries":
        bid = func_args.get("board_id")
        qs = await get_board_queries(bid) if bid else []
        return {"queries": qs, "count": len(qs)}
    elif func_name == "get_query_code":
        qid = func_args.get("query_id")
        return await get_query_code(qid) if qid else {"error": "Missing query_id"}
    elif func_name == "get_board_code":
        bid = func_args.get("board_id")
        return await get_board_code(bid) if bid else {"error": "Missing board_id"}
    elif func_name == "search_board_code":
        return await search_board_code(
            board_id=func_args.get("board_id", ""),
            search_term=func_args.get("search_term", ""),
            context_lines=func_args.get("context_lines", 3),
        )
    elif func_name == "search_query_code":
        return await search_query_code(
            query_id=func_args.get("query_id", ""),
            search_term=func_args.get("search_term", ""),
            context_lines=func_args.get("context_lines", 3),
        )
    elif func_name == "create_or_update_query":
        return await create_or_update_query(
            board_id=func_args.get("board_id"),
            query_name=func_args.get("query_name"),
            python_code=func_args.get("python_code"),
            description=func_args.get("description", ""),
            query_id=func_args.get("query_id"),
        )
    elif func_name == "delete_query":
        qid = func_args.get("query_id")
        return await delete_query(qid) if qid else {"error": "Missing query_id"}
    elif func_name == "get_datastore_schema":
        return await get_datastore_schema(
            datastore_id=func_args.get("datastore_id"),
            dataset=func_args.get("dataset"),
            table=func_args.get("table"),
        )
    elif func_name == "run_query":
        result = await run_query(func_args.get("python_code", ""))
        if result.get("success") and result.get("row_count", 0) == 0:
            result["warning"] = (
                "ZERO ROWS RETURNED. This likely means the query has incorrect column names, "
                "table names, or filter conditions. You MUST investigate."
            )
        return result
    elif func_name == "execute_query_direct":
        return await execute_query_direct(
            datastore_id=func_args.get("datastore_id", ""),
            sql_query=func_args.get("sql_query", ""),
            limit=func_args.get("limit", 100),
        )
    elif func_name == "create_or_update_datastore":
        return await create_or_update_datastore(
            name=func_args.get("name", ""),
            ds_type=func_args.get("type", ""),
            config=func_args.get("config", {}),
            datastore_id=func_args.get("datastore_id"),
            user_id=user_id,
            org_id=org_id,
        )
    elif func_name == "test_datastore":
        ds_id = func_args.get("datastore_id")
        return await test_datastore_tool(ds_id) if ds_id else {"error": "Missing datastore_id"}
    elif func_name == "save_keyfile":
        return await save_keyfile_tool(
            json_content=func_args.get("json_content", ""),
            user_id=user_id,
            filename=func_args.get("filename", "keyfile.json"),
        )
    else:
        return {"error": f"Unknown function: {func_name}"}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/models")
async def list_models():
    return get_available_models()


@router.post("/board-helper")
async def board_helper(
    request: Request,
    code: Optional[str] = Body(default=""),
    user_prompt: str = Body(...),
    chat: List[Dict[str, str]] = Body(default=[]),
    gemini_api_key: Optional[str] = Body(default=None),
    context: str = Body(default="board"),
    datastore_id: Optional[str] = Body(default=None),
    query_id: Optional[str] = Body(default=None),
    model: str = Body(default=DEFAULT_MODEL),
):
    user = await get_optional_user(request)
    user_id = str(user["id"]) if user else None

    try:
        if context == "query":
            return await exploration_helper_with_testing(
                code, user_prompt, chat, datastore_id, query_id, model, user_id
            )

        system_instruction = BOARD_SYSTEM_INSTRUCTION
        code_type = "board HTML"

        if code:
            user_message = (
                f"User request: {user_prompt}\n\n"
                f"Current {code_type} (edit this to fulfill the request):\n\n{code}"
            )
        else:
            user_message = (
                f"User request: {user_prompt}\n\n"
                "Generate a new board HTML from scratch that fulfills this request, "
                "using the board builder patterns (Alpine.js, boardManager, canvasWidget, KPI/chart widgets). "
                "Use a clean grid layout: position widgets with 20px gaps."
            )

        messages = _chat_to_messages(chat[-50:])
        messages.append({"role": "user", "content": user_message})

        resp = await call_llm(model, messages, system_instruction=system_instruction, temperature=0.3)
        await _log_usage(user_id, None, resp)

        if resp.finish_reason == "MAX_TOKENS":
            raise HTTPException(status_code=502, detail="Response was too long. Use the streaming endpoint.")
        if resp.finish_reason in ("SAFETY", "RECITATION", "OTHER"):
            raise HTTPException(status_code=502, detail=f"Response blocked ({resp.finish_reason}).")

        raw_text = resp.text or ""
        if not raw_text:
            raise HTTPException(status_code=502, detail="AI returned no content")

        edited_code = strip_markdown_code_block(raw_text.strip())
        return {"code": edited_code, "message": f"I've updated the {code_type} based on your request."}

    except Exception as e:
        import traceback; traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"AI helper failed: {str(e)}")


@router.post("/board-helper-stream")
async def board_helper_stream(
    request: Request,
    code: Optional[str] = Body(default=""),
    user_prompt: str = Body(...),
    chat: List[Dict[str, str]] = Body(default=[]),
    gemini_api_key: Optional[str] = Body(default=None),
    context: str = Body(default="board"),
    board_id: Optional[str] = Body(default=None),
    datastore_id: Optional[str] = Body(default=None),
    query_id: Optional[str] = Body(default=None),
    max_tool_iterations: int = Body(default=200),
    temperature: float = Body(default=0.3),
    model: str = Body(default=DEFAULT_MODEL),
    chat_id: Optional[str] = Body(default=None),
    organization_id: Optional[str] = Body(default=None),
    uploaded_file_paths: Optional[List[str]] = Body(default=None),
):
    user = await get_optional_user(request)
    user_id = str(user["id"]) if user else None

    async def generate_stream() -> AsyncGenerator[str, None]:
        try:
            # --- Query context ---
            if context == "query":
                async for event in exploration_helper_stream(
                    code, user_prompt, chat, datastore_id, query_id, board_id,
                    max_tool_iterations, temperature, model, user_id, chat_id,
                ):
                    yield f"data: {json.dumps(event)}\n\n"
                return

            # --- Datastore context ---
            if context == "datastore":
                async for event in _datastore_context_stream(
                    user_prompt, chat, datastore_id, organization_id,
                    max_tool_iterations, temperature, model, user_id, chat_id,
                    uploaded_file_paths,
                ):
                    yield f"data: {json.dumps(event)}\n\n"
                return

            # --- General context (home, portal, etc.) ---
            if context == "general":
                async for event in _general_context_stream(
                    user_prompt, chat, organization_id,
                    max_tool_iterations, temperature, model, user_id, chat_id,
                    uploaded_file_paths,
                ):
                    yield f"data: {json.dumps(event)}\n\n"
                return

            # --- Board context (default) ---
            yield f"data: {json.dumps({'type': 'thinking', 'content': 'Analyzing your request...'})}\n\n"
            await asyncio.sleep(0.1)

            system_instruction = BOARD_SYSTEM_INSTRUCTION
            board_queries = []

            context_info = ""
            if board_id:
                context_info = (
                    f"\n\n=== CURRENT CONTEXT ===\nCURRENT_BOARD_ID = '{board_id}'\n"
                    "(Use this value for the board_id parameter in create_or_update_query)\n"
                )
                board_queries = await get_board_queries(board_id)
                is_continuation = len(chat) > 0
                if board_queries:
                    context_info += "\nAvailable queries on this board:\n"
                    for q in board_queries:
                        context_info += f"- {q['name']} (ID: {q['id']}): {q.get('description', 'No description')}\n"
                    if is_continuation:
                        context_info += "\n--- QUERY CODE (for troubleshooting) ---\n"
                        for q in board_queries:
                            query_detail = await get_query_code(q["id"])
                            if "code" in query_detail:
                                context_info += f"\n[{q['name']}] (query_id: {q['id']}):\n{query_detail['code']}\n"
                        context_info += "--- END QUERY CODE ---\n"

                datastores = await get_available_datastores(user_id=user_id, org_id=organization_id)
                if datastores:
                    context_info += "\nAvailable datastores:\n"
                    for ds in datastores:
                        context_info += f"- {ds['name']} (Type: {ds['type']}, ID: {ds['id']})\n"
                    context_info += "(Use the ID value for @datastore in query code)\n"
                context_info += "===================\n"
                system_instruction = system_instruction + context_info

            user_message = f"User request: {user_prompt}"
            if code:
                user_message += "\n\nNote: Current board code is available if needed."
            if len(user_prompt.strip()) < 30 and board_queries:
                user_message += (
                    f"\n\nNote: There are {len(board_queries)} queries on this board. "
                    "If the request is unclear, use list_board_queries and get_query_code."
                )

            messages = _chat_to_messages(chat[-50:])
            messages.append({"role": "user", "content": user_message})

            model_info = get_model_info(model)
            use_tools = model_info.get("supports_tools", True)
            tools = get_tools_for_context("board") if use_tools else None

            yield f"data: {json.dumps({'type': 'progress', 'content': 'Processing your request...'})}\n\n"

            tool_iteration = 0
            edited_code = None
            raw_text = ""
            accumulated_text = ""
            query_created = False
            any_tools_called = False
            last_tool_results: List[dict] = []
            continuation_count = 0

            while tool_iteration < max_tool_iterations:
                tool_iteration += 1

                if tool_iteration == max_tool_iterations - 5:
                    yield f"data: {json.dumps({'type': 'progress', 'content': f'Approaching iteration limit ({tool_iteration}/{max_tool_iterations})...'})}\n\n"

                try:
                    resp = await call_llm(
                        model, messages,
                        system_instruction=system_instruction,
                        tools=tools,
                        temperature=temperature,
                    )
                except Exception as llm_err:
                    yield f"data: {json.dumps({'type': 'error', 'content': f'AI API error: {str(llm_err)}'})}\n\n"
                    return

                await _log_usage(user_id, chat_id, resp)

                if resp.finish_reason in ("SAFETY", "RECITATION", "OTHER"):
                    yield f"data: {json.dumps({'type': 'error', 'content': f'Response was blocked ({resp.finish_reason}). Try rephrasing.'})}\n\n"
                    return

                if resp.function_calls:
                    any_tools_called = True
                    last_tool_results = []

                    tc_list = [{"name": fc.name, "args": fc.args, "id": fc.name} for fc in resp.function_calls]
                    messages.append({"role": "assistant", "tool_calls": tc_list})

                    for fc in resp.function_calls:
                        yield f"data: {json.dumps({'type': 'tool_call', 'tool': fc.name, 'status': 'started', 'args': fc.args})}\n\n"

                        result = await _execute_tool(fc.name, fc.args, user_id=user_id, org_id=organization_id)

                        is_error = "error" in result and not result.get("success")
                        if fc.name == "execute_query_direct" and result.get("success"):
                            rc = result.get("returned_rows", 0)
                            msg = f"Executed query: {rc} rows returned"
                            if result.get("truncated"):
                                msg += f" (truncated from {result.get('total_rows', 0)} total rows)"
                            yield f"data: {json.dumps({'type': 'progress', 'content': msg})}\n\n"

                        if is_error:
                            yield f"data: {json.dumps({'type': 'tool_result', 'tool': fc.name, 'status': 'error', 'error': str(result.get('error', ''))})}\n\n"
                        else:
                            yield f"data: {json.dumps({'type': 'tool_result', 'tool': fc.name, 'status': 'success', 'result': result})}\n\n"

                        if fc.name in ("create_or_update_query", "delete_query") and result.get("success"):
                            query_created = True

                        last_tool_results.append({"tool": fc.name, "result": result})

                        messages.append({
                            "role": "tool",
                            "tool_call_id": fc.name,
                            "name": fc.name,
                            "content": json.dumps(result, default=str),
                        })

                    zero_rows = [
                        r for r in last_tool_results
                        if r["tool"] in ("run_query", "create_or_update_query")
                        and (
                            (r["result"].get("success") and r["result"].get("row_count", 0) == 0)
                            or (r["result"].get("test", {}).get("success") and r["result"].get("test", {}).get("row_count", 0) == 0)
                        )
                    ]
                    if zero_rows:
                        yield f"data: {json.dumps({'type': 'progress', 'content': 'Query returned 0 rows - investigating...'})}\n\n"
                        messages.append({
                            "role": "user",
                            "content": (
                                "WARNING: The query returned 0 rows. You MUST investigate why: "
                                "1) Call get_datastore_schema to check actual table/column names, "
                                "2) Try a simple SELECT * FROM dataset.table LIMIT 10, "
                                "3) Fix the query with correct names/filters."
                            ),
                        })

                    continue

                raw_text = resp.text or ""

                if resp.finish_reason == "MAX_TOKENS":
                    if tool_iteration < max_tool_iterations:
                        continuation_count += 1
                        if continuation_count == 1:
                            yield f"data: {json.dumps({'type': 'progress', 'content': 'Generating long response, please wait...'})}\n\n"
                        accumulated_text += raw_text
                        messages.append({"role": "assistant", "content": raw_text})
                        messages.append({"role": "user", "content": "Continue"})
                        continue
                    else:
                        yield f"data: {json.dumps({'type': 'error', 'content': 'Response too long and hit iteration limit.'})}\n\n"
                        return

                if accumulated_text:
                    raw_text = accumulated_text + raw_text
                    if continuation_count > 1:
                        yield f"data: {json.dumps({'type': 'progress', 'content': f'Completed long response ({continuation_count} parts)'})}\n\n"

                if not raw_text:
                    if any_tools_called and tool_iteration < max_tool_iterations:
                        yield f"data: {json.dumps({'type': 'progress', 'content': 'Continuing...'})}\n\n"
                        nudge = (
                            "You successfully created/updated a query. Now provide a brief summary."
                            if query_created
                            else "Continue with the original task. Complete the user's request."
                        )
                        messages.append({"role": "user", "content": nudge})
                        continue

                    if not any_tools_called and tool_iteration == 1:
                        yield f"data: {json.dumps({'type': 'progress', 'content': 'Gathering context...'})}\n\n"
                        messages.append({
                            "role": "user",
                            "content": (
                                "Gather context by calling the appropriate tools: "
                                "list_board_queries, get_query_code, etc. Then complete the request."
                            ),
                        })
                        continue

                    if not any_tools_called:
                        yield f"data: {json.dumps({'type': 'error', 'content': 'Could not understand your request. Please be more specific.'})}\n\n"
                        return

                    yield f"data: {json.dumps({'type': 'final', 'code': '', 'message': 'Tools executed but could not generate a final response.'})}\n\n"
                    return

                if query_created:
                    yield f"data: {json.dumps({'type': 'final', 'code': '', 'message': raw_text.strip()})}\n\n"
                    return

                edited_code = strip_markdown_code_block(raw_text.strip())

                is_html = edited_code and ("<!DOCTYPE" in edited_code or "<html" in edited_code.lower())
                is_explanation = len(edited_code) < 100 or ("<" not in edited_code)

                if not is_html or is_explanation:
                    if tool_iteration < max_tool_iterations:
                        yield f"data: {json.dumps({'type': 'progress', 'content': 'Received text instead of HTML, requesting code...'})}\n\n"
                        messages.append({"role": "assistant", "content": raw_text})
                        messages.append({
                            "role": "user",
                            "content": "Output ONLY the complete HTML code (starting with <!DOCTYPE html>) without explanations.",
                        })
                        continue
                    else:
                        yield f"data: {json.dumps({'type': 'error', 'content': 'AI returned text instead of HTML code.'})}\n\n"
                        return

                break

            if not edited_code:
                yield f"data: {json.dumps({'type': 'error', 'content': 'Failed to generate code'})}\n\n"
                return

            if tool_iteration >= max_tool_iterations:
                yield f"data: {json.dumps({'type': 'progress', 'content': f'Reached iteration limit ({max_tool_iterations}).'})}\n\n"

            yield f"data: {json.dumps({'type': 'progress', 'content': f'Code generated ({len(edited_code)} characters)'})}\n\n"
            yield f"data: {json.dumps({'type': 'thinking', 'content': 'Validating code structure...'})}\n\n"
            await asyncio.sleep(0.1)
            yield f"data: {json.dumps({'type': 'progress', 'content': 'Checking code...'})}\n\n"

            validation = validate_html(edited_code)
            vsummary = validation["summary"]
            if validation["valid"]:
                yield f"data: {json.dumps({'type': 'progress', 'content': f'{vsummary}'})}\n\n"
            else:
                yield f"data: {json.dumps({'type': 'progress', 'content': f'{vsummary}'})}\n\n"
            for err in validation.get("errors", []):
                yield f"data: {json.dumps({'type': 'progress', 'content': f'  Error: {err}'})}\n\n"
            for warn in validation.get("warnings", []):
                yield f"data: {json.dumps({'type': 'progress', 'content': f'  Warning: {warn}'})}\n\n"
            for info_item in validation.get("info", []):
                yield f"data: {json.dumps({'type': 'progress', 'content': f'  {info_item}'})}\n\n"

            if code:
                yield f"data: {json.dumps({'type': 'code_delta', 'old_code': code, 'new_code': edited_code})}\n\n"

            message = f"HTML {'validated and ' if validation['valid'] else ''}generated!"
            if validation.get("warnings"):
                message += f"\n\nNote: {len(validation['warnings'])} warning(s)."
            yield f"data: {json.dumps({'type': 'final', 'code': edited_code, 'message': message, 'validation': validation})}\n\n"

        except Exception as e:
            import traceback; traceback.print_exc()
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(generate_stream(), media_type="text/event-stream")


# ---------------------------------------------------------------------------
# Datastore context stream
# ---------------------------------------------------------------------------

async def _datastore_context_stream(
    user_prompt: str,
    chat: List[Dict[str, str]],
    datastore_id: Optional[str],
    organization_id: Optional[str],
    max_tool_iterations: int,
    temperature: float,
    model: str,
    user_id: Optional[str],
    chat_id: Optional[str],
    uploaded_file_paths: Optional[List[str]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    yield {"type": "thinking", "content": "Analyzing your request..."}
    await asyncio.sleep(0.1)

    system_instruction = DATASTORE_SYSTEM_INSTRUCTION
    context_info = ""

    if datastore_id:
        try:
            pool = get_pool()
            ds_row = await pool.fetchrow("SELECT id, name, type FROM datastores WHERE id = $1", datastore_id)
            if ds_row:
                context_info += f"\n\n=== CURRENT DATASTORE ===\nDatastore ID: {ds_row['id']}\nName: {ds_row['name']}\nType: {ds_row['type']}\n"
                context_info += "Use this datastore_id for schema exploration and queries.\n"
                context_info += "===================\n"
        except Exception:
            pass

    datastores = await get_available_datastores(user_id=user_id, org_id=organization_id)
    if datastores:
        context_info += "\nAll available datastores:\n"
        for ds in datastores:
            marker = " (CURRENT)" if ds["id"] == datastore_id else ""
            context_info += f"- {ds['name']} (Type: {ds['type']}, ID: {ds['id']}){marker}\n"

    if uploaded_file_paths:
        context_info += f"\nUploaded files available: {', '.join(uploaded_file_paths)}\n"
        context_info += "These files have been uploaded and their paths can be used in datastore config (e.g. keyfile_path).\n"

    system_instruction = system_instruction + context_info

    user_message = f"User request: {user_prompt}"

    messages = _chat_to_messages(chat[-50:])
    messages.append({"role": "user", "content": user_message})

    model_info = get_model_info(model)
    use_tools = model_info.get("supports_tools", True)
    tools = get_tools_for_context("datastore") if use_tools else None

    yield {"type": "progress", "content": "Processing..."}

    async for event in _tool_loop_stream(
        messages, system_instruction, tools, model, temperature,
        max_tool_iterations, user_id, chat_id, organization_id,
    ):
        yield event


# ---------------------------------------------------------------------------
# General context stream
# ---------------------------------------------------------------------------

async def _general_context_stream(
    user_prompt: str,
    chat: List[Dict[str, str]],
    organization_id: Optional[str],
    max_tool_iterations: int,
    temperature: float,
    model: str,
    user_id: Optional[str],
    chat_id: Optional[str],
    uploaded_file_paths: Optional[List[str]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    yield {"type": "thinking", "content": "Analyzing your request..."}
    await asyncio.sleep(0.1)

    system_instruction = GENERAL_SYSTEM_INSTRUCTION
    context_info = ""

    datastores = await get_available_datastores(user_id=user_id, org_id=organization_id)
    if datastores:
        context_info += "\nAvailable datastores:\n"
        for ds in datastores:
            context_info += f"- {ds['name']} (Type: {ds['type']}, ID: {ds['id']})\n"

    if uploaded_file_paths:
        context_info += f"\nUploaded files available: {', '.join(uploaded_file_paths)}\n"

    system_instruction = system_instruction + context_info

    user_message = f"User request: {user_prompt}"

    messages = _chat_to_messages(chat[-50:])
    messages.append({"role": "user", "content": user_message})

    model_info = get_model_info(model)
    use_tools = model_info.get("supports_tools", True)
    tools = get_tools_for_context("general") if use_tools else None

    yield {"type": "progress", "content": "Processing..."}

    async for event in _tool_loop_stream(
        messages, system_instruction, tools, model, temperature,
        max_tool_iterations, user_id, chat_id, organization_id,
    ):
        yield event


# ---------------------------------------------------------------------------
# Shared tool loop for non-board contexts (datastore, general)
# ---------------------------------------------------------------------------

async def _tool_loop_stream(
    messages: list,
    system_instruction: str,
    tools: Optional[list],
    model: str,
    temperature: float,
    max_tool_iterations: int,
    user_id: Optional[str],
    chat_id: Optional[str],
    organization_id: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    tool_iteration = 0
    any_tools_called = False
    accumulated_text = ""
    continuation_count = 0

    while tool_iteration < max_tool_iterations:
        tool_iteration += 1

        try:
            resp = await call_llm(
                model, messages,
                system_instruction=system_instruction,
                tools=tools,
                temperature=temperature,
            )
        except Exception as llm_err:
            yield {"type": "error", "content": f"AI API error: {str(llm_err)}"}
            return

        await _log_usage(user_id, chat_id, resp)

        if resp.finish_reason in ("SAFETY", "RECITATION", "OTHER"):
            yield {"type": "error", "content": f"Response was blocked ({resp.finish_reason}). Try rephrasing."}
            return

        if resp.function_calls:
            any_tools_called = True
            tc_list = [{"name": fc.name, "args": fc.args, "id": fc.name} for fc in resp.function_calls]
            messages.append({"role": "assistant", "tool_calls": tc_list})

            for fc in resp.function_calls:
                yield {"type": "tool_call", "tool": fc.name, "status": "started", "args": fc.args}

                result = await _execute_tool(fc.name, fc.args, user_id=user_id, org_id=organization_id)
                is_error = "error" in result and not result.get("success")

                if fc.name == "execute_query_direct" and result.get("success"):
                    rc = result.get("returned_rows", 0)
                    msg = f"Executed query: {rc} rows returned"
                    yield {"type": "progress", "content": msg}

                if is_error:
                    yield {"type": "tool_result", "tool": fc.name, "status": "error", "error": str(result.get("error", ""))}
                else:
                    yield {"type": "tool_result", "tool": fc.name, "status": "success", "result": result}

                messages.append({
                    "role": "tool",
                    "tool_call_id": fc.name,
                    "name": fc.name,
                    "content": json.dumps(result, default=str),
                })
            continue

        raw_text = resp.text or ""

        if resp.finish_reason == "MAX_TOKENS":
            if tool_iteration < max_tool_iterations:
                continuation_count += 1
                accumulated_text += raw_text
                messages.append({"role": "assistant", "content": raw_text})
                messages.append({"role": "user", "content": "Continue"})
                continue
            else:
                yield {"type": "error", "content": "Response too long."}
                return

        if accumulated_text:
            raw_text = accumulated_text + raw_text

        if not raw_text:
            if any_tools_called and tool_iteration < max_tool_iterations:
                messages.append({"role": "user", "content": "Provide a brief summary of what was done."})
                continue
            if not any_tools_called and tool_iteration == 1:
                messages.append({"role": "user", "content": "Use the available tools to help answer. Then provide a summary."})
                continue
            yield {"type": "final", "code": "", "message": "No response generated."}
            return

        yield {"type": "final", "code": "", "message": raw_text.strip()}
        return

    yield {"type": "error", "content": f"Reached maximum iterations ({max_tool_iterations})."}


# ---------------------------------------------------------------------------
# Exploration (query) helpers
# ---------------------------------------------------------------------------

async def exploration_helper_stream(
    code: str,
    user_prompt: str,
    chat: List[Dict[str, str]],
    datastore_id: Optional[str],
    query_id: Optional[str],
    board_id: Optional[str] = None,
    max_tool_iterations: int = 200,
    temperature: float = 0.3,
    model: str = DEFAULT_MODEL,
    user_id: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    max_attempts = 5

    schema_info = None
    if datastore_id:
        try:
            yield {"type": "thinking", "content": "I need to understand your database schema first..."}
            await asyncio.sleep(0.1)
            yield {"type": "progress", "content": "Fetching database schema..."}
            pool = get_pool()
            ds_row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", datastore_id)
            if ds_row:
                datastore = dict(ds_row)
                if datastore["type"] == "bigquery":
                    schema_result = await get_bigquery_schema(datastore, None, None)
                    datasets = schema_result.get("datasets", [])
                    schema_parts = ["BigQuery project datasets:"]
                    for ds in datasets:
                        tables = ds.get("tables", [])
                        schema_parts.append(f"\nDataset: {ds['name']} ({len(tables)} tables)")
                        for t in tables[:20]:
                            schema_parts.append(f"  - {ds['name']}.{t['name']}")
                    schema_info = "\n".join(schema_parts)
                    yield {"type": "progress", "content": f"Found {len(datasets)} BigQuery datasets with tables"}
                elif datastore["type"] == "postgres":
                    schema_result = await get_sql_schema(datastore, None, None)
                    schemas = schema_result.get("schemas", [])
                    schema_parts = ["PostgreSQL schemas:"]
                    for s in schemas:
                        schema_parts.append(f"  - {s['name']}")
                    schema_info = "\n".join(schema_parts)
                    yield {"type": "progress", "content": f"Found {len(schemas)} PostgreSQL schemas"}
        except Exception as e:
            yield {"type": "progress", "content": f"Schema fetch failed: {str(e)}"}

    context_info = ""
    try:
        datastores = await get_available_datastores()
        if datastores:
            context_info += "\n\nAvailable datastores:\n"
            for ds in datastores:
                context_info += f"- {ds['name']} (Type: {ds['type']}, ID: {ds['id']})\n"
    except Exception as e:
        yield {"type": "progress", "content": f"Could not fetch datastores: {str(e)}"}

    if board_id:
        try:
            bqs = await get_board_queries(board_id)
            if bqs:
                context_info += f"\n\nCONTEXT: Working on a query for board ID '{board_id}'. Other queries:\n"
                for q in bqs:
                    if query_id and q["id"] == query_id:
                        continue
                    context_info += f"- {q['name']} (ID: {q['id']}): {q.get('description', 'No description')}\n"
        except Exception as e:
            yield {"type": "progress", "content": f"Could not fetch board context: {str(e)}"}

    if query_id:
        try:
            pool = get_pool()
            query_row = await pool.fetchrow("SELECT name, description, board_id FROM board_queries WHERE id = $1", query_id)
            if query_row:
                qname = query_row.get("name", "query")
                context_info = f"\n\nCONTEXT: Editing query '{qname}' (ID: {query_id})." + context_info
                if not board_id and query_row.get("board_id"):
                    board_id = str(query_row["board_id"])
                    bqs = await get_board_queries(board_id)
                    if bqs:
                        context_info += f"\n\nThis query belongs to board ID '{board_id}'. Other queries:\n"
                        for q in bqs:
                            if q["id"] == query_id:
                                continue
                            context_info += f"- {q['name']} (ID: {q['id']}): {q.get('description', 'No description')}\n"
        except Exception as e:
            yield {"type": "progress", "content": f"Could not fetch query info: {str(e)}"}

    model_info = get_model_info(model)
    use_tools = model_info.get("supports_tools", True)
    tools = get_tools_for_context("query") if use_tools else None

    for attempt in range(1, max_attempts + 1):
        try:
            if attempt == 1:
                yield {"type": "thinking", "content": "Now I'll write Python code to fulfill your request..."}
            else:
                yield {"type": "thinking", "content": "Let me fix the error and try again..."}
            await asyncio.sleep(0.1)
            yield {"type": "progress", "content": "Generating Python code..."}

            if code:
                user_message = f"User request: {user_prompt}\n\nCurrent Python code:\n\n{code}"
            else:
                user_message = f"User request: {user_prompt}\n\nGenerate new Python query code from scratch using the @node comment structure."
            if schema_info:
                user_message += f"\n\nAvailable database schema:\n{schema_info}\n\nIMPORTANT: Use fully qualified table names."
            if context_info:
                user_message += context_info
            if attempt > 1 and "last_error" in dir():
                if attempt == 2:
                    user_message += f"\n\nPrevious attempt failed: {last_error}\n\nTry a SIMPLER query."
                else:
                    user_message += f"\n\nMultiple attempts failed. Last error: {last_error}\n\nTry SELECT * FROM dataset.table LIMIT 10."

            messages = _chat_to_messages(chat[-50:])
            messages.append({"role": "user", "content": user_message})

            generated_code = None
            tool_iteration = 0
            accumulated_text = ""
            continuation_count = 0

            while tool_iteration < max_tool_iterations:
                tool_iteration += 1
                if tool_iteration == max_tool_iterations - 5:
                    yield {"type": "progress", "content": f"Approaching iteration limit ({tool_iteration}/{max_tool_iterations})..."}

                try:
                    resp = await call_llm(
                        model, messages,
                        system_instruction=EXPLORATION_SYSTEM_INSTRUCTION,
                        tools=tools,
                        temperature=0.2 if attempt > 1 else temperature,
                    )
                except Exception as llm_err:
                    raise Exception(f"AI API error: {str(llm_err)}")

                await _log_usage(user_id, chat_id, resp)

                if resp.finish_reason in ("SAFETY", "RECITATION", "OTHER"):
                    raise Exception(f"Response was blocked ({resp.finish_reason}). Try rephrasing.")

                if resp.function_calls:
                    tc_list = [{"name": fc.name, "args": fc.args, "id": fc.name} for fc in resp.function_calls]
                    messages.append({"role": "assistant", "tool_calls": tc_list})

                    for fc in resp.function_calls:
                        yield {"type": "tool_call", "tool": fc.name, "status": "started", "args": fc.args}

                        result = await _execute_tool(fc.name, fc.args, user_id=user_id)
                        is_err = "error" in result and not result.get("success")

                        if fc.name == "execute_query_direct" and result.get("success"):
                            rc = result.get("returned_rows", 0)
                            msg = f"Executed query: {rc} rows returned"
                            if result.get("truncated"):
                                msg += f" (truncated from {result.get('total_rows', 0)} total)"
                            yield {"type": "progress", "content": msg}

                        if is_err:
                            yield {"type": "tool_result", "tool": fc.name, "status": "error", "error": str(result.get("error", ""))}
                        else:
                            yield {"type": "tool_result", "tool": fc.name, "status": "success", "result": result}

                        messages.append({
                            "role": "tool",
                            "tool_call_id": fc.name,
                            "name": fc.name,
                            "content": json.dumps(result, default=str),
                        })
                    continue

                raw_text = resp.text or ""

                if resp.finish_reason == "MAX_TOKENS":
                    if tool_iteration < max_tool_iterations:
                        continuation_count += 1
                        if continuation_count == 1:
                            yield {"type": "progress", "content": "Generating long response, please wait..."}
                        accumulated_text += raw_text
                        messages.append({"role": "assistant", "content": raw_text})
                        messages.append({"role": "user", "content": "Continue"})
                        continue
                    else:
                        raise Exception("Response too long. Try smaller tasks.")

                if accumulated_text:
                    raw_text = accumulated_text + raw_text
                    if continuation_count > 1:
                        yield {"type": "progress", "content": f"Completed long response ({continuation_count} parts)"}

                if raw_text.strip():
                    stripped = raw_text.strip()
                    if not stripped.startswith(("#", "@", "import", "from", "def", "class", "if", "for", "while", "try", "with", "result", "```")):
                        yield {"type": "progress", "content": "AI returned text instead of code, reprompting..."}
                        messages.append({"role": "assistant", "content": raw_text})
                        messages.append({"role": "user", "content": "Output ONLY valid Python code. No explanations."})
                        continue
                    generated_code = strip_markdown_code_block(stripped)
                    break

                if tool_iteration < max_tool_iterations:
                    yield {"type": "progress", "content": "No response from AI, reprompting..."}
                    messages.append({"role": "user", "content": "Generate the Python query code now. Output ONLY valid Python code."})
                    continue

                raise Exception("Too many iterations without code generation")

            if not generated_code:
                raise Exception("Failed to generate code")

            yield {"type": "progress", "content": f"Code generated ({len(generated_code)} characters)"}
            if code:
                yield {"type": "code_delta", "old_code": code, "new_code": generated_code}
            else:
                yield {"type": "code_delta", "old_code": "", "new_code": generated_code}

            if query_id or datastore_id:
                yield {"type": "thinking", "content": "Let me test this code to make sure it works..."}
                await asyncio.sleep(0.1)
                yield {"type": "progress", "content": "Testing the generated code..."}
                test_query_id = query_id
                if not test_query_id:
                    yield {"type": "progress", "content": "  Creating temporary test query..."}
                    pool = get_pool()
                    board_row = await pool.fetchrow("SELECT id FROM boards LIMIT 1")
                    if board_row:
                        temp_row = await pool.fetchrow(
                            "INSERT INTO board_queries (name, board_id, python_code, ui_map) VALUES ($1,$2,$3,$4) RETURNING id",
                            f"_test_{int(time.time())}", str(board_row["id"]), generated_code, "{}",
                        )
                        if temp_row:
                            test_query_id = str(temp_row["id"])

                if test_query_id:
                    try:
                        pool = get_pool()
                        await pool.execute("UPDATE board_queries SET python_code=$1 WHERE id=$2", generated_code, test_query_id)
                        test_response = await asyncio.to_thread(
                            requests.post,
                            "http://localhost:8000/explore",
                            json={"query_id": test_query_id, "args": {}, "datastore_id": datastore_id},
                            timeout=30,
                        )
                        if test_response.ok:
                            test_data = test_response.json()
                            row_count = test_data.get("count", 0)
                            yield {"type": "progress", "content": f"Test passed! Query returned {row_count} rows"}
                            yield {"type": "test_result", "success": True, "row_count": row_count}
                            if test_data.get("table") and len(test_data["table"]) > 0:
                                first_row = test_data["table"][0]
                                sample = ", ".join([f"{k}={v}" for k, v in list(first_row.items())[:3]])
                                yield {"type": "progress", "content": f"  Sample: {sample}..."}
                            yield {
                                "type": "final", "code": generated_code,
                                "message": f"Code generated and tested{' on first try' if attempt == 1 else ' after fixing'}.",
                                "test_passed": True, "attempts": attempt,
                            }
                            return
                        else:
                            error_data = test_response.json()
                            last_error = error_data.get("detail", "Unknown error")
                            yield {"type": "progress", "content": f"Test failed: {last_error}"}
                            yield {"type": "test_result", "success": False, "error": last_error}
                            if attempt == max_attempts:
                                yield {
                                    "type": "needs_user_input", "code": generated_code, "error": last_error,
                                    "message": f"Still failing:\n\n```\n{last_error}\n```\n\nHow would you like to proceed?",
                                    "test_passed": False,
                                }
                                yield {
                                    "type": "final", "code": generated_code,
                                    "message": f"Code generated but not working yet.\n\nError: {last_error[:200]}...",
                                    "test_passed": False, "attempts": attempt, "error": last_error,
                                }
                                return
                            continue
                    except Exception as test_error:
                        last_error = str(test_error)
                        yield {"type": "progress", "content": f"Test execution error: {last_error}"}
                        yield {"type": "test_result", "success": False, "error": last_error}
                        if attempt == max_attempts:
                            yield {
                                "type": "needs_user_input", "code": generated_code, "error": last_error,
                                "message": f"Testing failed:\n\n```\n{last_error}\n```", "test_passed": False,
                            }
                            yield {
                                "type": "final", "code": generated_code,
                                "message": f"Code generated but testing failed.\n\nError: {last_error[:200]}...",
                                "test_passed": False, "attempts": attempt, "error": last_error,
                            }
                            return
                        continue
            else:
                yield {"type": "progress", "content": "Skipping test (no datastore or query ID)"}
                yield {
                    "type": "final", "code": generated_code,
                    "message": "Code generated (not tested).", "test_passed": None, "attempts": attempt,
                }
                return

        except Exception as e:
            last_error = str(e)
            yield {"type": "progress", "content": f"Generation error: {last_error}"}
            if attempt == max_attempts:
                yield {
                    "type": "needs_user_input", "error": last_error,
                    "message": f"Error:\n\n```\n{last_error}\n```\n\nTry a different approach?", "test_passed": False,
                }
                yield {"type": "error", "content": f"Failed after {max_attempts} attempts. Last error: {last_error}"}
                return
            continue


async def exploration_helper_with_testing(
    code: str,
    user_prompt: str,
    chat: List[Dict[str, str]],
    datastore_id: Optional[str],
    query_id: Optional[str],
    model: str = DEFAULT_MODEL,
    user_id: Optional[str] = None,
) -> Dict[str, Any]:
    progress_log: List[str] = []
    max_attempts = 5

    schema_info = None
    if datastore_id:
        try:
            progress_log.append("Fetching database schema...")
            pool = get_pool()
            ds_row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", datastore_id)
            if ds_row:
                datastore = dict(ds_row)
                if datastore["type"] == "bigquery":
                    schema_result = await get_bigquery_schema(datastore, None, None)
                    schema_info = f"BigQuery datasets: {', '.join([d['name'] for d in schema_result.get('datasets', [])])}"
                    progress_log.append(f"Found {len(schema_result.get('datasets', []))} datasets")
                elif datastore["type"] == "postgres":
                    schema_result = await get_sql_schema(datastore, None, None)
                    schema_info = f"PostgreSQL schemas: {', '.join([s['name'] for s in schema_result.get('schemas', [])])}"
                    progress_log.append(f"Found {len(schema_result.get('schemas', []))} schemas")
        except Exception as e:
            progress_log.append(f"Schema fetch failed: {str(e)}")

    for attempt in range(1, max_attempts + 1):
        try:
            progress_log.append(f"\nAttempt {attempt}/{max_attempts}: Generating Python code...")

            if code:
                user_message = f"User request: {user_prompt}\n\nCurrent Python code:\n\n{code}"
            else:
                user_message = f"User request: {user_prompt}\n\nGenerate new Python query code from scratch."
            if schema_info:
                user_message += f"\n\nSchema:\n{schema_info}\n\nUse fully qualified table names."
            if attempt > 1 and "last_error" in dir():
                user_message += f"\n\nPrevious error: {last_error}\n\nTry a simpler approach."

            messages = _chat_to_messages(chat[-50:])
            messages.append({"role": "user", "content": user_message})

            resp = await call_llm(model, messages, system_instruction=EXPLORATION_SYSTEM_INSTRUCTION, temperature=0.2 if attempt > 1 else 0.3)
            await _log_usage(user_id, None, resp)

            raw_text = resp.text or ""
            if not raw_text:
                raise Exception("AI returned no content")

            generated_code = strip_markdown_code_block(raw_text.strip())
            progress_log.append(f"Code generated ({len(generated_code)} characters)")

            if query_id or datastore_id:
                progress_log.append("Testing...")
                test_query_id = query_id
                if not test_query_id:
                    pool = get_pool()
                    board_row = await pool.fetchrow("SELECT id FROM boards LIMIT 1")
                    if board_row:
                        temp_row = await pool.fetchrow(
                            "INSERT INTO board_queries (name, board_id, python_code, ui_map) VALUES ($1,$2,$3,$4) RETURNING id",
                            f"_test_{int(time.time())}", str(board_row["id"]), generated_code, "{}",
                        )
                        if temp_row:
                            test_query_id = str(temp_row["id"])
                if test_query_id:
                    try:
                        pool = get_pool()
                        await pool.execute("UPDATE board_queries SET python_code=$1 WHERE id=$2", generated_code, test_query_id)
                        test_response = await asyncio.to_thread(
                            requests.post, "http://localhost:8000/explore",
                            json={"query_id": test_query_id, "args": {}, "datastore_id": datastore_id}, timeout=30,
                        )
                        if test_response.ok:
                            test_data = test_response.json()
                            row_count = test_data.get("count", 0)
                            progress_log.append(f"Test passed! {row_count} rows")
                            return {
                                "code": generated_code,
                                "message": "Code generated and tested successfully!\n\n" + "\n".join(progress_log),
                                "progress": progress_log, "test_passed": True, "attempts": attempt,
                            }
                        else:
                            last_error = test_response.json().get("detail", "Unknown error")
                            progress_log.append(f"Test failed: {last_error}")
                            if attempt == max_attempts:
                                return {
                                    "code": generated_code,
                                    "message": f"Generated but failed after {max_attempts} attempts.\n\n" + "\n".join(progress_log),
                                    "progress": progress_log, "test_passed": False, "attempts": attempt, "error": last_error,
                                }
                            continue
                    except Exception as te:
                        last_error = str(te)
                        progress_log.append(f"Test error: {last_error}")
                        if attempt == max_attempts:
                            return {
                                "code": generated_code, "message": "Testing failed.\n\n" + "\n".join(progress_log),
                                "progress": progress_log, "test_passed": False, "attempts": attempt, "error": last_error,
                            }
                        continue
            else:
                return {
                    "code": generated_code, "message": "Code generated (not tested).\n\n" + "\n".join(progress_log),
                    "progress": progress_log, "test_passed": None, "attempts": attempt,
                }
        except Exception as e:
            last_error = str(e)
            progress_log.append(f"Error: {last_error}")
            if attempt == max_attempts:
                raise Exception(f"Failed after {max_attempts} attempts. Last: {last_error}")
            continue

    raise Exception("Unexpected error in exploration helper")


# ---------------------------------------------------------------------------
# Other AI endpoints
# ---------------------------------------------------------------------------

@router.post("/get-schema")
async def get_schema(
    datastore_id: Optional[str] = Body(default=None),
    connector_id: Optional[str] = Body(default=None),
    database: Optional[str] = Body(default=None),
    table: Optional[str] = Body(default=None),
):
    try:
        ds_id = datastore_id or connector_id
        if not ds_id:
            raise HTTPException(status_code=400, detail="Missing datastore_id or connector_id")
        pool = get_pool()
        ds_row = await pool.fetchrow("SELECT * FROM datastores WHERE id = $1", ds_id)
        if not ds_row:
            raise HTTPException(status_code=404, detail="Datastore not found")
        datastore = dict(ds_row)
        if datastore["type"] == "bigquery":
            return await get_bigquery_schema(datastore, database, table)
        elif datastore["type"] == "postgres":
            return await get_sql_schema(datastore, database, table)
        else:
            raise HTTPException(status_code=400, detail=f"Unsupported datastore type: {datastore['type']}")
    except Exception as e:
        import traceback; traceback.print_exc()
        if isinstance(e, HTTPException):
            raise e
        raise HTTPException(status_code=500, detail=f"Schema retrieval failed: {str(e)}")


@router.post("/generate-chat-title")
async def generate_chat_title(
    request: Request,
    user_prompt: str = Body(...),
    context: str = Body(default="general"),
    gemini_api_key: Optional[str] = Body(default=None),
    model: str = Body(default=DEFAULT_MODEL),
):
    user = await get_optional_user(request)
    user_id = str(user["id"]) if user else None

    try:
        system_instruction = (
            "You are a title generator. Given a user's message, create a concise, descriptive title (2-5 words max).\n"
            "Rules:\n- Output ONLY the title text\n- No quotes, no markdown\n- Keep it short (2-5 words)\n- Use title case"
        )
        messages = [{"role": "user", "content": f"Generate a title for this chat:\n\n{user_prompt}"}]
        resp = await call_llm(model, messages, system_instruction=system_instruction, temperature=0.3, max_tokens=50)
        await _log_usage(user_id, None, resp)

        raw_title = (resp.text or "").strip().strip('"').strip("'")
        if not raw_title:
            return {"title": user_prompt[:40] + ("..." if len(user_prompt) > 40 else "")}
        if len(raw_title) > 50:
            raw_title = raw_title[:47] + "..."
        return {"title": raw_title}
    except Exception as e:
        print(f"Title generation error: {e}")
        words = user_prompt.split()[:5]
        return {"title": " ".join(words) + ("..." if len(user_prompt.split()) > 5 else "")}
