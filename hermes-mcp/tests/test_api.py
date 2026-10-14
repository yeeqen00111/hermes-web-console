import asyncio


def _tools():
    from hermes_mcp import server
    return {t.name for t in asyncio.run(server.app.list_tools())}


def test_tool_surface():
    names = _tools()
    for expected in ["ping", "list_connections", "query", "get_schema_map",
                     "describe_table", "list_schemas", "ask"]:
        assert expected in names, names


def test_app_name():
    from hermes_mcp import server
    assert server.app.name == "hermes-mcp"