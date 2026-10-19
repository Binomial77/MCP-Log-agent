import os
import asyncio

from dotenv import load_dotenv

from langgraph.graph import StateGraph, MessagesState, START
from langgraph.prebuilt import ToolNode, tools_condition

from langchain_core.messages import (
    SystemMessage,
    ToolMessage,
)

from langchain_openai import ChatOpenAI
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv()

BROKER_URL = os.getenv("BROKER_URL")
API_KEY = os.getenv("API_KEY")
MODEL = os.getenv("MODEL_NAME")


if not BROKER_URL:
    raise ValueError("BROKER_URL is missing from the .env file")

if not API_KEY:
    raise ValueError("API_KEY is missing from the .env file")

if not MODEL:
    raise ValueError("MODEL_NAME is missing from the .env file")

BASE_URL = (
    BROKER_URL
    .rstrip("/")
    .removesuffix("/chat/completions")
)


print(f"Using model: {MODEL}")
print(f"Using broker URL: {BASE_URL}")


SYSTEM = """
You are a company support assistant with access to MCP tools.

IMPORTANT TOOL-CALLING RULES:

- Use the available tools whenever company-specific or log-specific
  information is required.
- When a tool is required, use the native tool-calling interface.
- Do not return a tool request as JSON inside normal response content.
- Request only ONE tool at a time.
- Never request multiple tools in one response.
- Wait for the result of one tool before requesting another tool.
- Never show tool calls, tool-call JSON, or Python code in the final answer.
- After receiving a tool result, analyze it carefully.
- If a tool result provides useful evidence, stop calling tools and provide
  the final answer.
- If a search result is empty or unclear, call search_log again with a
  different relevant keyword.
- Never repeat the same filename and keyword combination.
- Never invent company rules, filenames, log entries, errors, or tool
  results.


COMPANY POLICY WORKFLOW:

When the user asks about working hours, leave policy, holidays, dress code,
company policies, company rules, or related company information:

1. Call get_company_rules.
2. Wait for the tool result.
3. Answer only from the rules returned by the tool.
4. Do not call log-related tools for company-policy questions.
5. If the requested information is unavailable, say:
   "I could not find this information in the company rules."


TECHNICAL PROBLEM WORKFLOW:

When the user reports a technical problem:

1. Call get_log_file_names.
2. Wait for the available filenames.
3. Select the filename most relevant to the user's problem.
4. Call search_log using that filename and ONE useful keyword.
5. Wait for the search result.
6. If useful evidence is found, stop searching and provide a final answer.
7. If no useful evidence is found, call search_log again using a different
   keyword.
8. Never repeat the same filename and keyword combination.
9. Do not call get_log_file_names again if the filenames were already
   returned.
10. Stop after a reasonable number of attempts.


RELEVANT SEARCH KEYWORDS MAY INCLUDE:

- error
- failed
- failure
- timeout
- exception
- refused
- unavailable
- denied
- slow


FINAL ANSWER RULES:

For technical problems, always give the final answer in exactly this format:

Hypothesis: <most likely cause or state that no cause was found>

Evidence: <relevant log entries or state that no evidence was found>

Suggested next step: <recommended action>


If no relevant evidence is found, respond with:

Hypothesis: I could not determine the cause from the available logs.

Evidence: No relevant errors were found in the searched log files.

Suggested next step: Confirm that the affected service is writing to the
expected log file, and reproduce the problem while monitoring the logs.
"""


MAX_TOOL_RESULTS = 5

async def main():

    client = MultiServerMCPClient(
        {
            "support": {
                "url": "http://localhost:30000/mcp",
                "transport": "streamable_http",
            }
        }
    )

    tools = await client.get_tools()

    if not tools:
        raise RuntimeError(
            "Connected to the MCP server, but no tools were discovered."
        )

    print("\nConnected to MCP server.")
    print("Available tools:")

    for tool in tools:
        print(f"  - {tool.name}")

    base_llm = ChatOpenAI(
        model=MODEL,
        base_url=BASE_URL,
        api_key=API_KEY,
        temperature=0,
        max_retries=3,
        timeout=60,
    )

    llm_with_tools = base_llm.bind_tools(
        tools,
        # parallel_tool_calls=False,
    )

    llm_without_tools = base_llm

    async def agent(state: MessagesState):
        messages = state["messages"]
        tool_results = [
            message
            for message in messages
            if isinstance(message, ToolMessage)
        ]
        if len(tool_results) >= MAX_TOOL_RESULTS:

            forced_final_prompt = f"""
{SYSTEM}

MAXIMUM TOOL LIMIT REACHED:

You have reached the maximum number of permitted tool executions.

You do not have access to additional tools for this response.

Using only the original user question and the existing tool results,
provide the final answer now.

Do not request another tool.
Do not return a tool call.
Do not return JSON.
Do not return Python code.

For a technical problem, use exactly this structure:

Hypothesis: <most likely cause>

Evidence: <relevant log evidence>

Suggested next step: <recommended action>

If no useful evidence was found, return:

Hypothesis: I could not determine the cause from the available logs.

Evidence: No relevant errors were found in the searched log files.

Suggested next step: Confirm that the affected service is writing to the
expected log file, and reproduce the problem while monitoring the logs.
"""

            final_response = await llm_without_tools.ainvoke(
                [
                    SystemMessage(content=forced_final_prompt),
                    *messages,
                ]
            )

            print("\nMaximum tool-execution limit reached.")
            print("Forcing the model to provide a final answer.")

            return {
                "messages": [final_response]
            }

        response = await llm_with_tools.ainvoke(
            [
                SystemMessage(content=SYSTEM),
                *messages,
            ]
        )

        return {
            "messages": [response]
        }

    graph_builder = StateGraph(MessagesState)

    graph_builder.add_node(
        "agent",
        agent,
    )

    graph_builder.add_node(
        "tools",
        ToolNode(tools),
    )

    graph_builder.add_edge(
        START,
        "agent",
    )
    graph_builder.add_conditional_edges(
        "agent",
        tools_condition,
    )

    graph_builder.add_edge(
        "tools",
        "agent",
    )

    graph = graph_builder.compile()
    print("\nCompany Support Agent started.")
    print("Type 'exit' to stop.\n")

    while True:

        question = input("Ask: ").strip()

        if not question:
            continue

        if question.lower() == "exit":
            print("\nAgent stopped.")
            break

        try:
            result = await graph.ainvoke(
                {
                    "messages": [
                        ("user", question)
                    ]
                }
            )

            print("\nTool execution history:")

            found_tool_call = False

            for message in result["messages"]:

                tool_calls = (
                    getattr(message, "tool_calls", None) or []
                )

                for call in tool_calls:
                    found_tool_call = True

                    tool_name = call.get(
                        "name",
                        "unknown_tool",
                    )

                    tool_arguments = call.get(
                        "args",
                        {},
                    )

                    print(
                        f"  - {tool_name}"
                        f"({tool_arguments})"
                    )

            if not found_tool_call:
                print("  No tools were called.")

            final_message = result["messages"][-1]

            if isinstance(final_message.content, str):
                final_answer = final_message.content.strip()
            else:
                final_answer = str(
                    final_message.content
                ).strip()

            print("\nAnswer:")

            if final_answer:
                print(final_answer)
            else:
                print(
                    "The agent completed without producing "
                    "a final text response."
                )

            print()

        except Exception as error:
            print("\nAgent execution failed:")
            print(
                f"{type(error).__name__}: {error}\n"
            )

if __name__ == "__main__":
    asyncio.run(main())