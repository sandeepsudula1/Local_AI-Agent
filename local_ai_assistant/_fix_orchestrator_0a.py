"""Replace the step-0a block in orchestrator.py with the atomic handle_response() version."""
import pathlib, sys

src_path = pathlib.Path("pipelines/orchestrator.py")
src = src_path.read_text(encoding="utf-8")

OLD = (
    "        # \u2500\u2500 0a. Permission-request response detection \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "        # When the previous turn issued a REQUEST_PERMISSION prompt, the next\n"
    '        # user message is "yes" / "no" / "allow" / "deny".\n'
    "        try:\n"
    "            from core.permission_store import permission_store as _perm_store\n"
    '            _approve_words = {"yes", "y", "allow", "ok", "okay", "sure", "grant", "grant access", "yep", "yup"}\n'
    '            _deny_words = {"no", "n", "deny", "nope", "nah", "cancel", "reject", "decline"}\n'
    "            _lower = text.strip().lower()\n"
    "            if _perm_store.has_pending():\n"
    "                _pending_folder, _pending_query = _perm_store.get_pending()\n"
    "                if _pending_folder is None:\n"
    "                    # Request expired between has_pending() and get_pending()\n"
    "                    pass\n"
    "                elif _lower in _approve_words:\n"
    "                    _perm_store.grant(_pending_folder)\n"
    "                    _perm_store.clear_pending()\n"
    "                    # Also clear any CLARIFY pending_query so it can't interfere\n"
    "                    _mem_clr = _get_memory()\n"
    "                    if _mem_clr is not None:\n"
    "                        try:\n"
    "                            _mem_clr.clear_pending_query()\n"
    "                        except Exception:\n"
    "                            pass\n"
    "                    # Index the newly-granted folder so documents are searchable\n"
    '                    _index_msg = ""\n'
    "                    try:\n"
    "                        from services.document_indexer_service import document_indexer_service as _dis\n"
    '                        log.info("Indexing newly-granted folder: %s", _pending_folder)\n'
    "                        _indexed = _dis.index_folder(_pending_folder, wait=True, timeout=120.0)\n"
    "                        if not _indexed:\n"
    "                            _index_msg = (\n"
    '                                "\\n\\n\u26a0\ufe0f No documents were found in that ffolder "\n'
    '                                "(it may be empty or contain unsupported file types)."\n'
    "                            )\n"
    "                    except Exception as _ie:\n"
    '                        log.warning("index_folder failed for %s: %s", _pending_folder, _ie)\n'
    "                    _grant_msg = (\n"
    '                        f"\u2705 Access granted.\\n\\n"\n'
    '                        f"You can now query files from:\\n"\n'
    '                        f"\U0001f4c1 {_pending_folder}\\n\\n"\n'
    '                        f"Continuing your request\u2026{_index_msg}"\n'
    "                    )\n"
    "                    # Re-run the original query now that access is granted and folder is indexed\n"
    "                    _rerun_resp = self.run(_pending_query)\n"
    '                    _rerun_resp.answer = _grant_msg + "\\n\\n" + (_rerun_resp.answer or "")\n'
    '                    _rerun_resp.intent = "PERMISSION_GRANTED"\n'
    "                    _rerun_resp.latency_ms = (time.perf_counter() - t0) * 1_000\n"
    "                    return _rerun_resp\n"
    "                elif _lower in _deny_words:\n"
    "                    _perm_store.clear_pending()\n"
    "                    _deny_resp = AgentResponse(\n"
    '                        answer="Access request denied. I will not use that folder.",\n'
    '                        intent="PERMISSION_DENIED",\n'
    "                    )\n"
    "                    _deny_resp.latency_ms = (time.perf_counter() - t0) * 1_000\n"
    "                    return _deny_resp\n"
    "                # User said something other than yes/no \u2014 treat as a new query;\n"
    "                # the permission request stays pending so they can still answer it.\n"
    "            elif _perm_store.is_expired():\n"
    "                # Pending request existed but timed out \u2014 inform user once and clear\n"
    "                _perm_store.clear_pending()\n"
    "                _exp_resp = AgentResponse(\n"
    "                    answer=(\n"
    '                        "\u23f0 The previous permission request has expired (5-minute timeout). "\n'
    '                        "Please re-send your original request to start a new permission prompt."\n'
    "                    ),\n"
    '                    intent="PERMISSION_EXPIRED",\n'
    "                )\n"
    "                _exp_resp.latency_ms = (time.perf_counter() - t0) * 1_000\n"
    "                return _exp_resp\n"
    "            elif _lower in _approve_words or _lower in _deny_words:\n"
    "                # Standalone yes/no with no pending and no expired request \u2014\n"
    "                # user confirmed something that isn't waiting for a response.\n"
    "                _no_pending_resp = AgentResponse(\n"
    '                    answer="There is no pending permission request.",\n'
    '                    intent="NO_PENDING_PERMISSION",\n'
    "                )\n"
    "                _no_pending_resp.latency_ms = (time.perf_counter() - t0) * 1_000\n"
    "                return _no_pending_resp\n"
    "        except Exception as _perm_exc:\n"
    '            log.debug("Permission-request response handling failed: %s", _perm_exc)'
)

NEW = (
    "        # \u2500\u2500 0a. Permission-request response detection \u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\n"
    "        # Must run BEFORE intent classification and every other pipeline stage\n"
    "        # so that \"yes\" / \"no\" can NEVER reach the intent classifier.\n"
    "        #\n"
    "        # handle_response() is atomic (single lock) \u2014 no multi-step race and no\n"
    "        # broad except that could silently swallow the intercept.\n"
    "        from core.permission_store import permission_store as _perm_store\n"
    "        _perm_action, _perm_folder, _perm_orig_query = _perm_store.handle_response(text)\n"
    "\n"
    '        if _perm_action == "GRANT":\n'
    "            # Clear any CLARIFY pending_query so it can\u2019t interfere with the re-run\n"
    "            _mem_clr = _get_memory()\n"
    "            if _mem_clr is not None:\n"
    "                try:\n"
    "                    _mem_clr.clear_pending_query()\n"
    "                except Exception:\n"
    "                    pass\n"
    "            # Index the newly-granted folder so its documents become searchable\n"
    '            _index_msg = ""\n'
    "            try:\n"
    "                from services.document_indexer_service import document_indexer_service as _dis\n"
    '                log.info("Indexing newly-granted folder: %s", _perm_folder)\n'
    "                _indexed = _dis.index_folder(_perm_folder, wait=True, timeout=120.0)\n"
    "                if not _indexed:\n"
    "                    _index_msg = (\n"
    '                        "\\n\\n\u26a0\ufe0f No documents were found in that folder "\n'
    '                        "(it may be empty or contain unsupported file types)."\n'
    "                    )\n"
    "            except Exception as _ie:\n"
    '                log.warning("index_folder failed for %s: %s", _perm_folder, _ie)\n'
    "            _grant_msg = (\n"
    '                f"\u2705 Access granted.\\n\\n"\n'
    '                f"You can now query files from:\\n"\n'
    '                f"\U0001f4c1 {_perm_folder}\\n\\n"\n'
    '                f"Continuing your request\u2026{_index_msg}"\n'
    "            )\n"
    "            _rerun_resp = self.run(_perm_orig_query)\n"
    '            _rerun_resp.answer = _grant_msg + "\\n\\n" + (_rerun_resp.answer or "")\n'
    '            _rerun_resp.intent = "PERMISSION_GRANTED"\n'
    "            _rerun_resp.latency_ms = (time.perf_counter() - t0) * 1_000\n"
    "            return _rerun_resp\n"
    "\n"
    '        if _perm_action == "DENY":\n'
    "            _deny_resp = AgentResponse(\n"
    '                answer="Access request denied. I will not use that folder.",\n'
    '                intent="PERMISSION_DENIED",\n'
    "            )\n"
    "            _deny_resp.latency_ms = (time.perf_counter() - t0) * 1_000\n"
    "            return _deny_resp\n"
    "\n"
    '        if _perm_action == "EXPIRED":\n'
    "            _exp_resp = AgentResponse(\n"
    "                answer=(\n"
    '                    "\u23f0 The previous permission request has expired (5-minute timeout). "\n'
    '                    "Please re-send your original request to start a new permission prompt."\n'
    "                ),\n"
    '                intent="PERMISSION_EXPIRED",\n'
    "            )\n"
    "            _exp_resp.latency_ms = (time.perf_counter() - t0) * 1_000\n"
    "            return _exp_resp\n"
    "\n"
    '        if _perm_action == "NO_PENDING":\n'
    "            _no_pending_resp = AgentResponse(\n"
    '                answer="There is no pending permission request.",\n'
    '                intent="NO_PENDING_PERMISSION",\n'
    "            )\n"
    "            _no_pending_resp.latency_ms = (time.perf_counter() - t0) * 1_000\n"
    "            return _no_pending_resp\n"
    "\n"
    '        # _perm_action == "NONE" \u2014 normal text; fall through to the full pipeline'
)

if OLD in src:
    src2 = src.replace(OLD, NEW, 1)
    src_path.write_text(src2, encoding="utf-8")
    print("Replaced OK")
else:
    print("NOT FOUND", file=sys.stderr)
    # Find the nearest anchor to help debug
    idx = src.find("0a. Permission-request response detection")
    print(repr(src[idx:idx+120]), file=sys.stderr)
    sys.exit(1)
