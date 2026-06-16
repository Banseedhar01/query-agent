"""Save the agent graph as a PNG image."""

from src.agent.graph import build_graph

graph = build_graph()
png_bytes = graph.get_graph(xray=True).draw_mermaid_png()

output_path = "agent_graph.png"
with open(output_path, "wb") as f:
    f.write(png_bytes)

print(f"Graph saved to {output_path}")
