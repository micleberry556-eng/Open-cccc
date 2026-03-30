defmodule Nexora.Org.OrgChart do
  @moduledoc """
  Manages organizational structure for AI agent teams.

  Implements Paperclip-style org charts with:
  - Hierarchical roles (CEO, CTO, Engineers, etc.)
  - Reporting lines between agents
  - Department/team grouping
  - Role-based task delegation
  """
  use GenServer

  @table :org_chart

  defmodule Role do
    defstruct [
      :id,
      :title,
      :department,
      :agent_id,
      :reports_to,
      :description,
      :permissions,
      direct_reports: [],
      created_at: nil
    ]
  end

  # --- Client API ---

  def start_link(_opts) do
    GenServer.start_link(__MODULE__, [], name: __MODULE__)
  end

  def add_role(attrs) do
    GenServer.call(__MODULE__, {:add_role, attrs})
  end

  def remove_role(role_id) do
    GenServer.call(__MODULE__, {:remove_role, role_id})
  end

  def assign_agent(role_id, agent_id) do
    GenServer.call(__MODULE__, {:assign_agent, role_id, agent_id})
  end

  def unassign_agent(role_id) do
    GenServer.call(__MODULE__, {:unassign_agent, role_id})
  end

  def get_role(role_id) do
    GenServer.call(__MODULE__, {:get_role, role_id})
  end

  def list_roles do
    GenServer.call(__MODULE__, :list_roles)
  end

  def get_hierarchy do
    GenServer.call(__MODULE__, :get_hierarchy)
  end

  def get_reports(role_id) do
    GenServer.call(__MODULE__, {:get_reports, role_id})
  end

  def get_manager(role_id) do
    GenServer.call(__MODULE__, {:get_manager, role_id})
  end

  def get_departments do
    GenServer.call(__MODULE__, :get_departments)
  end

  # --- Server ---

  @impl true
  def init(_) do
    :ets.new(@table, [:named_table, :public, :set])

    # Seed default org structure
    seed_defaults()
    {:ok, %{}}
  end

  @impl true
  def handle_call({:add_role, attrs}, _from, state) do
    role = %Role{
      id: attrs[:id] || generate_id(),
      title: attrs[:title] || "New Role",
      department: attrs[:department] || "General",
      agent_id: attrs[:agent_id],
      reports_to: attrs[:reports_to],
      description: attrs[:description] || "",
      permissions: attrs[:permissions] || [:read, :execute],
      created_at: DateTime.utc_now()
    }

    :ets.insert(@table, {role.id, role})

    # Update parent's direct_reports
    if role.reports_to do
      case :ets.lookup(@table, role.reports_to) do
        [{_, parent}] ->
          updated = %{parent | direct_reports: [role.id | parent.direct_reports] |> Enum.uniq()}
          :ets.insert(@table, {parent.id, updated})
        _ -> :ok
      end
    end

    broadcast({:role_added, role})
    {:reply, {:ok, role}, state}
  end

  def handle_call({:remove_role, role_id}, _from, state) do
    case :ets.lookup(@table, role_id) do
      [{_, role}] ->
        # Remove from parent's direct_reports
        if role.reports_to do
          case :ets.lookup(@table, role.reports_to) do
            [{_, parent}] ->
              updated = %{parent | direct_reports: List.delete(parent.direct_reports, role_id)}
              :ets.insert(@table, {parent.id, updated})
            _ -> :ok
          end
        end
        :ets.delete(@table, role_id)
        broadcast({:role_removed, role_id})
        {:reply, :ok, state}
      [] ->
        {:reply, {:error, :not_found}, state}
    end
  end

  def handle_call({:assign_agent, role_id, agent_id}, _from, state) do
    case :ets.lookup(@table, role_id) do
      [{_, role}] ->
        updated = %{role | agent_id: agent_id}
        :ets.insert(@table, {role_id, updated})
        broadcast({:agent_assigned, role_id, agent_id})
        {:reply, {:ok, updated}, state}
      [] ->
        {:reply, {:error, :not_found}, state}
    end
  end

  def handle_call({:unassign_agent, role_id}, _from, state) do
    case :ets.lookup(@table, role_id) do
      [{_, role}] ->
        updated = %{role | agent_id: nil}
        :ets.insert(@table, {role_id, updated})
        {:reply, {:ok, updated}, state}
      [] ->
        {:reply, {:error, :not_found}, state}
    end
  end

  def handle_call({:get_role, role_id}, _from, state) do
    case :ets.lookup(@table, role_id) do
      [{_, role}] -> {:reply, role, state}
      [] -> {:reply, nil, state}
    end
  end

  def handle_call(:list_roles, _from, state) do
    roles = :ets.tab2list(@table) |> Enum.map(fn {_, r} -> r end)
    {:reply, roles, state}
  end

  def handle_call(:get_hierarchy, _from, state) do
    roles = :ets.tab2list(@table) |> Enum.map(fn {_, r} -> r end)
    roots = Enum.filter(roles, &is_nil(&1.reports_to))
    tree = Enum.map(roots, fn root -> build_tree(root, roles) end)
    {:reply, tree, state}
  end

  def handle_call({:get_reports, role_id}, _from, state) do
    roles = :ets.tab2list(@table) |> Enum.map(fn {_, r} -> r end)
    reports = Enum.filter(roles, &(&1.reports_to == role_id))
    {:reply, reports, state}
  end

  def handle_call({:get_manager, role_id}, _from, state) do
    case :ets.lookup(@table, role_id) do
      [{_, role}] when not is_nil(role.reports_to) ->
        case :ets.lookup(@table, role.reports_to) do
          [{_, manager}] -> {:reply, manager, state}
          [] -> {:reply, nil, state}
        end
      _ -> {:reply, nil, state}
    end
  end

  def handle_call(:get_departments, _from, state) do
    roles = :ets.tab2list(@table) |> Enum.map(fn {_, r} -> r end)
    departments = roles
      |> Enum.group_by(& &1.department)
      |> Enum.map(fn {dept, roles} -> %{name: dept, count: length(roles), roles: roles} end)
    {:reply, departments, state}
  end

  # --- Private ---

  defp build_tree(role, all_roles) do
    children = Enum.filter(all_roles, &(&1.reports_to == role.id))
    %{
      role: role,
      children: Enum.map(children, fn child -> build_tree(child, all_roles) end)
    }
  end

  defp seed_defaults do
    roles = [
      %{id: "ceo", title: "CEO", department: "Executive", description: "Chief Executive Officer - sets company vision and strategy", reports_to: nil, permissions: [:read, :write, :execute, :admin]},
      %{id: "cto", title: "CTO", department: "Engineering", description: "Chief Technology Officer - leads technical direction", reports_to: "ceo", permissions: [:read, :write, :execute]},
      %{id: "cmo", title: "CMO", department: "Marketing", description: "Chief Marketing Officer - leads growth and marketing", reports_to: "ceo", permissions: [:read, :write, :execute]},
      %{id: "eng-lead", title: "Lead Engineer", department: "Engineering", description: "Senior engineer - reviews code and mentors team", reports_to: "cto", permissions: [:read, :write, :execute]},
      %{id: "eng-1", title: "Software Engineer", department: "Engineering", description: "Builds features and fixes bugs", reports_to: "eng-lead", permissions: [:read, :write, :execute]},
      %{id: "eng-2", title: "Software Engineer", department: "Engineering", description: "Builds features and fixes bugs", reports_to: "eng-lead", permissions: [:read, :write, :execute]},
      %{id: "content", title: "Content Strategist", department: "Marketing", description: "Creates marketing content and copy", reports_to: "cmo", permissions: [:read, :write, :execute]},
      %{id: "researcher", title: "Research Analyst", department: "Research", description: "Conducts market research and analysis", reports_to: "ceo", permissions: [:read, :execute]}
    ]

    # Insert directly into ETS instead of calling add_role/1 (which would
    # deadlock because we are inside init/1 and the GenServer is not yet
    # ready to handle calls).
    for attrs <- roles do
      role = %Role{
        id: attrs[:id],
        title: attrs[:title] || "New Role",
        department: attrs[:department] || "General",
        agent_id: nil,
        reports_to: attrs[:reports_to],
        description: attrs[:description] || "",
        permissions: attrs[:permissions] || [:read, :execute],
        created_at: DateTime.utc_now()
      }

      :ets.insert(@table, {role.id, role})
    end

    # Wire up direct_reports after all roles are inserted.
    for [{_, role}] <- Enum.map(:ets.tab2list(@table), &[&1]) do
      if role.reports_to do
        case :ets.lookup(@table, role.reports_to) do
          [{_, parent}] ->
            updated = %{parent | direct_reports: [role.id | parent.direct_reports] |> Enum.uniq()}
            :ets.insert(@table, {parent.id, updated})
          _ -> :ok
        end
      end
    end
  end

  defp broadcast(event) do
    Phoenix.PubSub.broadcast(Nexora.PubSub, "org", event)
  end

  defp generate_id do
    :crypto.strong_rand_bytes(6) |> Base.url_encode64(padding: false) |> String.downcase()
  end
end
