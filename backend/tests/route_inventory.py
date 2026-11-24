"""Test-only inventory of effective FastAPI routes, including hidden routes."""

from fastapi import routing


def api_operations(app):
    """Yield (effective route, HTTP method) pairs for the access-control tests."""
    # FastAPI 0.140 resolves included routers lazily. Use the same route
    # contexts as FastAPI's OpenAPI builder, before schema visibility filtering.
    if hasattr(routing, "iter_route_contexts"):
        routes = (
            context for context in routing.iter_route_contexts(app.routes)
            if isinstance(context.original_route, routing.APIRoute)
        )
    else:
        routes = (route for route in app.routes if isinstance(route, routing.APIRoute))
    for route in routes:
        if not route.path.startswith("/static"):
            for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
                yield route, method
