from __future__ import annotations

from vermay.tool_registry import ToolRegistry
from vermay.tooling import ToolArgs, structured_tool
from vermay.tool_metadata import ApprovalPolicy, ExecutionScope, SideEffectLevel, ToolCategory
from pydantic import Field

from .forecast import weather_forecast


class WeatherForecastArgs(ToolArgs):
    location: str = Field(
        description="City or location, for example 'Shanghai', 'San Francisco', or 'Beijing'."
    )
    days: int = Field(default=3, ge=1, le=3, description="Number of forecast days, between 1 and 3.")


def register_weather_tools(registry: ToolRegistry) -> None:
    registry.register(
        structured_tool(
            func=weather_forecast,
            name="weather_forecast",
            description=(
                "Get current weather and a 1-3 day forecast for a city or location. "
                "Use this for weather, temperature, rain, wind, or forecast questions."
            ),
            args_schema=WeatherForecastArgs,
            dangerous=False,
            category=ToolCategory.WEATHER,
            execution_scope=ExecutionScope.EXTERNAL_NETWORK,
            read_only=True,
            side_effect_level=SideEffectLevel.NONE,
            approval_policy=ApprovalPolicy.AUTO,
            redaction_required=False,
        )
    )
