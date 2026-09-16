# Hart-to-Generator Specific Mapping for MPGeneratorBase

## Overview

This feature allows MP (multi-processor) test generators to pin specific sequences to specific harts, enabling reproducible and topology-aware MP test generation.

The design uses a **two-layer model**:
1. **cpuconfig** is the source of truth for physical cluster topology
2. **Each MP sequence** can be explicitly mapped to one or more real `hart_ids`
3. **Framework resolves** the `cluster_id` for every mapped hart from cpuconfig automatically

This keeps hardware topology separate from test intent while allowing each sequence to run on exactly the harts it needs.

---

## Quick Start

### 1. Define Cluster Topology in cpuconfig

Add a `cluster_topology` block to your target-specific cpuconfig:

```json
{
  "cluster_topology": {
    "clusters": {
      "0": { "hart_ids": [0, 1, 2, 3] },
      "1": { "hart_ids": [8, 9, 10, 11] }
    }
  }
}
```

### 2. Use hart_ids in Your MP Generator

```python
from riescue.voyager2.apis.mp_generator_base import MPGeneratorBase
from riescue.voyager2.apis.plugin_generator_base import PluginGeneratorBase

class MyMPGenerator(MPGeneratorBase):
    pass

class ProducerSequence(PluginGeneratorBase):
    def sequence(self, **kwargs):
        # Access resolved hart context
        print(f"Running on hart {self.current_hart_id}, cluster {self.current_cluster_id}")
        self.add_instruction()

class ConsumerSequence(PluginGeneratorBase):
    def sequence(self, **kwargs):
        self.add_instruction()

# In your plugin:
mp = MyMPGenerator()
mp.add_hart_sequence(ProducerSequence, hart_ids=[0, 1])   # Pin to harts 0 and 1
mp.add_hart_sequence(ConsumerSequence, hart_ids=[8, 9])   # Pin to harts 8 and 9
self.generate(mp)
```

---

## API Reference

### MPGeneratorBase.add_hart_sequence()

```python
def add_hart_sequence(
    self,
    sequence,
    core_count: int = 1,
    hart_ids: Optional[List[int]] = None,
    cluster_id: Optional[int] = None,
)
```

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `sequence` | `type` | PluginGeneratorBase subclass to run |
| `core_count` | `int` | Number of cores (used only when `hart_ids` is None for random assignment). Default: 1 |
| `hart_ids` | `List[int]` | Explicit list of real mhartid values to pin this sequence to. When provided, one sequence instance runs on each listed hart. |
| `cluster_id` | `int` | Optional validation constraint. If provided, all `hart_ids` must belong to this cluster (raises error otherwise). Does NOT define topology - that comes from cpuconfig. |

**Behaviors:**
- `hart_ids` provided: Pin sequence to exactly these harts (one instance per hart)
- `hart_ids` omitted: Random selection from all available cores (legacy behavior)

**Note:** `hart_ids` are real mhartid values, not sequential core indices.

**Examples:**

```python
# Pin specific harts
mp.add_hart_sequence(Producer, hart_ids=[0, 1])

# Pin with cluster validation (error if harts not in cluster 0)
mp.add_hart_sequence(Producer, hart_ids=[0, 1], cluster_id=0)

# Legacy random assignment (backward compatible)
mp.add_hart_sequence(Worker, core_count=3)

# Mixed: explicit + random
mp.add_hart_sequence(Controller, hart_ids=[0])  # Pinned
mp.add_hart_sequence(Worker, core_count=2)       # Random from remaining
```

---

### Sequence Hart Context

When a sequence runs as part of an MP generator, it has access to:

| Attribute | Type | Description |
|-----------|------|-------------|
| `self.current_core` | `int` | Sequential core index (0 to num_cpus-1) |
| `self.current_hart_id` | `int` | Real mhartid value |
| `self.current_cluster_id` | `Optional[int]` | Cluster ID from cpuconfig (None if not configured) |

**Example:**

```python
class MySequence(PluginGeneratorBase):
    def sequence(self, **kwargs):
        hart = self.current_hart_id       # e.g., 8
        cluster = self.current_cluster_id  # e.g., 1

        # Generate cluster-specific behavior
        if cluster == 0:
            # Cluster 0 behavior
            self.add_instruction(...)
        else:
            # Other cluster behavior
            self.add_instruction(...)
```

---

### Resource Topology Accessors

The `Resource` singleton provides topology access methods:

```python
from riescue.voyager2.apis.resource import Resource

resource = Resource()

# Get full cluster topology (cluster_id -> [hart_ids])
topology = resource.get_cluster_topology()
# Returns: {0: [0, 1, 2, 3], 1: [8, 9, 10, 11]} or None if not configured

# Get cluster ID for a specific hart
cluster = resource.get_cluster_id(hart_id=8)
# Returns: 1 (or None if topology not configured)

# Get all harts in a cluster
harts = resource.get_harts_in_cluster(cluster_id=0)
# Returns: [0, 1, 2, 3]

# Convert between hart_id and core_index
core_idx = resource.hart_id_to_core_index(hart_id=8)  # Returns: 2
hart_id = resource.core_index_to_hart_id(core_index=2)  # Returns: 8
```

---

## cpuconfig Schema

### cluster_topology

```json
{
  "cluster_topology": {
    "clusters": {
      "<cluster_id>": {
        "hart_ids": [<hart_id>, <hart_id>, ...]
      }
    }
  }
}
```

**Validation Rules:**
- Every listed `hart_id` must be unique across all clusters
- Every configured runtime hart should belong to exactly one cluster
- Empty clusters are rejected
- Cluster IDs must be integers (string keys in JSON are converted)

**Shorthand Syntax:**

```json
{
  "cluster_topology": {
    "clusters": {
      "0": [0, 1, 2, 3],
      "1": [8, 9, 10, 11]
    }
  }
}
```

---

## Use Cases

### 1. Pin Producers and Consumers to Specific Harts

```python
mp = MyMPGenerator()
mp.add_hart_sequence(Producer, hart_ids=[0, 1])   # Producers on cluster 0
mp.add_hart_sequence(Consumer, hart_ids=[8, 9])   # Consumers on cluster 1
self.generate(mp)
```

### 2. Cluster-Aware Test Generation

```python
class ClusterAwareSequence(PluginGeneratorBase):
    def sequence(self, **kwargs):
        if self.current_cluster_id == 0:
            # Generate local memory access pattern
            pass
        else:
            # Generate remote memory access pattern
            pass
```

### 3. Validate Hart Assignment Against Cluster

```python
# This will raise an error if hart 8 is not in cluster 0
mp.add_hart_sequence(Producer, hart_ids=[8], cluster_id=0)
# ValueError: hart_id 8 belongs to cluster 1, not the specified cluster_id 0
```

### 4. Mixed Explicit and Random Assignment

```python
mp = MyMPGenerator()
mp.add_hart_sequence(Controller, hart_ids=[0])  # Pin controller to hart 0
mp.add_hart_sequence(Worker, core_count=3)       # 3 random workers (excluding hart 0)
self.generate(mp)
```

---

## Backward Compatibility

- Existing `add_hart_sequence(seq, core_count=N)` calls continue to work unchanged
- Default behavior (no `hart_ids`) remains random selection via `_assign_to_cores()`
- No changes to generated assembly output format
- Cluster topology in cpuconfig is optional - if absent, `get_cluster_id()` returns None

---

## Error Handling

| Error | Cause | Solution |
|-------|-------|----------|
| `hart_id X not in configured hart_ids` | Hart ID not in `--hart_ids` or cpuconfig | Use valid hart IDs from your configuration |
| `hart_id X is already assigned` | Same hart assigned to multiple sequences | Use unique harts per sequence |
| `hart_id X belongs to cluster Y, not Z` | Cluster validation failed | Verify hart belongs to expected cluster |
| `Cluster topology not configured` | Accessing cluster methods without cpuconfig | Add `cluster_topology` to cpuconfig |

---

## Example Files

See the following example files in the repository:

- **Plugin Example:** `riescue/voyager2/plugins/mp_example/hart_pinned_mp.py`
- **Plugin Config:** `riescue/voyager2/plugins/mp_example/hart_pinned_mp.json`
- **cpuconfig Example:** `riescue/voyager2/plugins/mp_example/example_cluster_cpuconfig.json`
- **Unit Tests:** `tests/voyager2/mp_hart_mapping_test.py`

---

## Running Tests

```bash
cd /proj_risc/user_dev/lelmiladi/riescue
./infra/container-run python3 -m unittest tests.voyager2.mp_hart_mapping_test -v
```

---

## Terminology

| Term | Definition |
|------|------------|
| **Core index** | Sequential position (0-based) used internally by MP machinery |
| **mhartid / hart_id** | Real hardware hart ID value (may be non-contiguous, e.g., 0, 1, 8, 9) |
| **Cluster** | A group of hart_ids that share physical topology (defined in cpuconfig) |
