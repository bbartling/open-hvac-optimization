Loop over data model in C with `Redland RDF`.

> NOTE: Experimental!!!

The Redland RDF libraries are a set of free, modular software libraries written in C that provide a high-level interface for the Resource Description Framework (RDF), allowing developers to store, query, and manipulate semantic data graphs. Created by Dave Beckett around the year 2000, Redland was developed to address the lack of a portable, industrial-strength RDF toolkit that could function independently of larger systems like Mozilla, whose internal RDF code was difficult to reuse at the time. The framework was designed from the start to be flexible and efficient, utilizing an object-based API that supports multiple storage backends (such as memory, hashes, or persistent databases) and offering language bindings for Python, Perl, PHP, and others. Over the years, it evolved into a mature suite that includes the Raptor syntax library for parsing and the Rasqal library for querying with SPARQL, becoming a foundational tool for semantic web development across various platforms.

### **Prerequisites (The Library)**

You need the **Redland RDF** development libraries installed to compile the C code.

Run this in your WSL terminal:

```bash
sudo apt update
sudo apt install libredland-dev pkg-config

```

---

### **High Level What This Code Does**

This C program (`resolve_by_class.c`) acts as a "Semantic Driver" for building automation.

1. **Loads** a Brick model (TTL file) into memory.
2. **Finds** a specific BACnet device (using the Instance Number provided).
3. **Resolves** specific Brick point classes (like `Supply_Air_Temperature_Sensor`) to their actual hardware addresses (BACnet Object IDs).
4. **Prints** the mapping so your control logic knows which point is which.

---

### **MORE Specifically the Code Does**

### 1. Loads a Brick model into memory

* **The Methods:** `librdf_new_world`, `librdf_new_storage`, `librdf_new_model`, `librdf_parser_parse_into_model`.
* **Under the Hood:**
* **Initialization:** The code allocates a `librdf_world` struct on the heap, which tracks the global state. It then creates a `librdf_storage` object explicitly passing `"memory"` as the argument. This tells Redland to allocate a large hash map or linked list in RAM to hold the graph, rather than connecting to a SQL database or disk file.
* **Parsing:** The parser reads your `.ttl` file byte-by-byte. For every triple it encounters (like `device a Equipment`), it `malloc`s three `librdf_node` structures (Subject, Predicate, Object). It then inserts pointers to these nodes into the storage backend.



### 2. Finds & 3. Resolves (The Query Engine)

* **The Methods:** `snprintf`, `librdf_new_query`, `librdf_query_execute`.
* **Under the Hood:**
* **String Construction:** You are manually building the SPARQL query string on the stack (in the `query` buffer) using `snprintf`. You inject the integer `3456789` directly into the string, making it a hardcoded constraint.
* **Compilation:** `librdf_new_query` parses your SPARQL string. If valid, it generates an internal query plan (a sequence of operations to perform on the graph).
* **Execution:** `librdf_query_execute` runs this plan against the `librdf_model`. It finds the node matching `device,3456789`, then traverses the pointers in memory: finding the "Reference" node, then the "Point" node, and checking if the "Point" has a `rdf:type` matching your requested classes.



### 4. Prints the mapping (The Fetch Loop)

* **The Methods:** `librdf_query_results_next`, `librdf_query_results_get_binding_value`, `librdf_free_node`.
* **Under the Hood (Memory Critical):**
* **The Cursor:** `librdf_query_execute` returns a `librdf_query_results` object, which is an iterator (a cursor) pointing to the current match.
* **Borrowing Memory:** When you call `librdf_query_results_get_binding_value`, Redland returns a pointer to a `librdf_node`. **Crucially**, it increments the **Reference Count** of that node. It is saying, "You are now holding this node."
* **Zero-Copy Access:** Your new helper function (`get_node_val`) peeks inside the struct (e.g., accessing `node->value.uri.string`) to read the data without `malloc`ing a new string. This is fast and efficient.
* **Manual Garbage Collection:** Because the reference count was incremented, you **must** call `librdf_free_node` at the end of the loop. This decrements the counter. If you forget this, the node remains "checked out" forever, causing a memory leak.



### 5. Teardown

* **The Methods:** `librdf_free_query`, `librdf_free_model`, `librdf_free_world`.
* **Under the Hood:** When `main` finishes, you call the free functions in reverse order. `librdf_free_world` is the final cleanup; it walks through the storage, checking reference counts. Any node with a count of 0 is `free`d from the heap.

---

### **Compiling**

Use `gcc` linked with `pkg-config` to automatically pull in the Redland flags.

```bash
gcc resolve_by_class.c -o resolve_by_class $(pkg-config --cflags --libs redland)

```

---

### **Usage**

**Syntax:**
`./resolve_by_class <model_file> <device_instance> <class_list>`

* `<model_file>`: Path to your `.ttl` file.
* `<device_instance>`: The BACnet Device ID (integer).
* `<class_list>`: Comma-separated list of Brick classes (no spaces).

**Example:**
To find the Supply Temp and Return Temp on Device **3456789**:

```bash
./resolve_by_class output_model_typed.ttl 3456789 Supply_Air_Temperature_Sensor,Return_Air_Temperature_Sensor

```

**Output:**

```text
class=https://brickschema.org/schema/Brick#Supply_Air_Temperature_Sensor object=analog-input,2 label=SA-T
class=https://brickschema.org/schema/Brick#Return_Air_Temperature_Sensor object=analog-input,4 label=RA-T

```