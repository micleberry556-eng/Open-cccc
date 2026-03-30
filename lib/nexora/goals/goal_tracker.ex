defmodule Nexora.Goals.GoalTracker do
  @moduledoc """
  Hierarchical goal tracking with mission-to-task tracing.

  Implements Paperclip-style goal alignment:
  Mission -> Objectives -> Key Results -> Tasks

  Every task carries full goal ancestry so agents
  understand the "why" behind their work.
  """
  use GenServer

  @table :goals

  defmodule Goal do
    defstruct [
      :id,
      :title,
      :description,
      :type,        # :mission | :objective | :key_result | :task
      :parent_id,
      :owner_id,    # agent_id or role_id
      :status,      # :active | :completed | :blocked | :cancelled
      :priority,    # :critical | :high | :medium | :low
      :progress,    # 0-100
      :due_date,
      children: [],
      metadata: %{},
      created_at: nil,
      updated_at: nil
    ]
  end

  # --- Client API ---

  def start_link(_opts) do
    GenServer.start_link(__MODULE__, [], name: __MODULE__)
  end

  def create(attrs) do
    GenServer.call(__MODULE__, {:create, attrs})
  end

  def update(goal_id, changes) do
    GenServer.call(__MODULE__, {:update, goal_id, changes})
  end

  def delete(goal_id) do
    GenServer.call(__MODULE__, {:delete, goal_id})
  end

  def get(goal_id) do
    GenServer.call(__MODULE__, {:get, goal_id})
  end

  def list do
    GenServer.call(__MODULE__, :list)
  end

  def list_by_type(type) do
    GenServer.call(__MODULE__, {:list_by_type, type})
  end

  def get_ancestry(goal_id) do
    GenServer.call(__MODULE__, {:get_ancestry, goal_id})
  end

  def get_tree do
    GenServer.call(__MODULE__, :get_tree)
  end

  def get_children(goal_id) do
    GenServer.call(__MODULE__, {:get_children, goal_id})
  end

  # --- Server ---

  @impl true
  def init(_) do
    :ets.new(@table, [:named_table, :public, :set])
    seed_defaults()
    {:ok, %{}}
  end

  @impl true
  def handle_call({:create, attrs}, _from, state) do
    goal = %Goal{
      id: attrs[:id] || generate_id(),
      title: attrs[:title] || "Untitled Goal",
      description: attrs[:description] || "",
      type: attrs[:type] || :task,
      parent_id: attrs[:parent_id],
      owner_id: attrs[:owner_id],
      status: attrs[:status] || :active,
      priority: attrs[:priority] || :medium,
      progress: attrs[:progress] || 0,
      due_date: attrs[:due_date],
      metadata: attrs[:metadata] || %{},
      created_at: DateTime.utc_now(),
      updated_at: DateTime.utc_now()
    }

    :ets.insert(@table, {goal.id, goal})

    if goal.parent_id do
      case :ets.lookup(@table, goal.parent_id) do
        [{_, parent}] ->
          updated = %{parent | children: [goal.id | parent.children] |> Enum.uniq()}
          :ets.insert(@table, {parent.id, updated})
        _ -> :ok
      end
    end

    broadcast({:goal_created, goal})
    {:reply, {:ok, goal}, state}
  end

  def handle_call({:update, goal_id, changes}, _from, state) do
    case :ets.lookup(@table, goal_id) do
      [{_, goal}] ->
        updated = Enum.reduce(changes, goal, fn {k, v}, acc -> Map.put(acc, k, v) end)
        updated = %{updated | updated_at: DateTime.utc_now()}
        :ets.insert(@table, {goal_id, updated})
        broadcast({:goal_updated, updated})
        {:reply, {:ok, updated}, state}
      [] ->
        {:reply, {:error, :not_found}, state}
    end
  end

  def handle_call({:delete, goal_id}, _from, state) do
    :ets.delete(@table, goal_id)
    {:reply, :ok, state}
  end

  def handle_call({:get, goal_id}, _from, state) do
    case :ets.lookup(@table, goal_id) do
      [{_, goal}] -> {:reply, goal, state}
      [] -> {:reply, nil, state}
    end
  end

  def handle_call(:list, _from, state) do
    goals = :ets.tab2list(@table) |> Enum.map(fn {_, g} -> g end)
    {:reply, goals, state}
  end

  def handle_call({:list_by_type, type}, _from, state) do
    goals = :ets.tab2list(@table)
      |> Enum.map(fn {_, g} -> g end)
      |> Enum.filter(&(&1.type == type))
    {:reply, goals, state}
  end

  def handle_call({:get_ancestry, goal_id}, _from, state) do
    ancestry = build_ancestry(goal_id, [])
    {:reply, ancestry, state}
  end

  def handle_call(:get_tree, _from, state) do
    all = :ets.tab2list(@table) |> Enum.map(fn {_, g} -> g end)
    roots = Enum.filter(all, &is_nil(&1.parent_id))
    tree = Enum.map(roots, fn root -> build_goal_tree(root, all) end)
    {:reply, tree, state}
  end

  def handle_call({:get_children, goal_id}, _from, state) do
    all = :ets.tab2list(@table) |> Enum.map(fn {_, g} -> g end)
    children = Enum.filter(all, &(&1.parent_id == goal_id))
    {:reply, children, state}
  end

  # --- Private ---

  defp build_ancestry(nil, acc), do: Enum.reverse(acc)
  defp build_ancestry(goal_id, acc) do
    case :ets.lookup(@table, goal_id) do
      [{_, goal}] -> build_ancestry(goal.parent_id, [goal | acc])
      [] -> Enum.reverse(acc)
    end
  end

  defp build_goal_tree(goal, all) do
    children = Enum.filter(all, &(&1.parent_id == goal.id))
    %{
      goal: goal,
      children: Enum.map(children, fn c -> build_goal_tree(c, all) end)
    }
  end

  defp seed_defaults do
    defaults = [
      %{id: "mission", title: "Build the best AI agent platform", type: :mission, priority: :critical, description: "Nexora mission: Create the most powerful, reliable, and developer-friendly AI agent command center"},
      %{id: "obj-1", title: "Multi-model agent orchestration", type: :objective, parent_id: "mission", priority: :high, description: "Support all major LLM providers with seamless switching"},
      %{id: "obj-2", title: "Enterprise-grade reliability", type: :objective, parent_id: "mission", priority: :high, description: "Leverage BEAM for fault tolerance and zero-downtime operations"},
      %{id: "kr-1", title: "Support 10+ LLM providers", type: :key_result, parent_id: "obj-1", priority: :medium, progress: 40},
      %{id: "kr-2", title: "99.99% agent uptime", type: :key_result, parent_id: "obj-2", priority: :high, progress: 80}
    ]

    # Insert directly into ETS to avoid deadlock (GenServer.call inside init).
    for attrs <- defaults do
      goal = %Goal{
        id: attrs[:id],
        title: attrs[:title] || "Untitled Goal",
        description: attrs[:description] || "",
        type: attrs[:type] || :task,
        parent_id: attrs[:parent_id],
        owner_id: nil,
        status: :active,
        priority: attrs[:priority] || :medium,
        progress: attrs[:progress] || 0,
        due_date: nil,
        metadata: %{},
        created_at: DateTime.utc_now(),
        updated_at: DateTime.utc_now()
      }

      :ets.insert(@table, {goal.id, goal})
    end

    # Wire up children after all goals are inserted.
    for [{_, goal}] <- Enum.map(:ets.tab2list(@table), &[&1]) do
      if goal.parent_id do
        case :ets.lookup(@table, goal.parent_id) do
          [{_, parent}] ->
            updated = %{parent | children: [goal.id | parent.children] |> Enum.uniq()}
            :ets.insert(@table, {parent.id, updated})
          _ -> :ok
        end
      end
    end
  end

  defp broadcast(event) do
    Phoenix.PubSub.broadcast(Nexora.PubSub, "goals", event)
  end

  defp generate_id do
    :crypto.strong_rand_bytes(6) |> Base.url_encode64(padding: false) |> String.downcase()
  end
end
