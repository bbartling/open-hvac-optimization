#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include <redland.h>

static const char* BRICK_NS = "https://brickschema.org/schema/Brick#";

static void die(const char* msg) {
  fprintf(stderr, "ERROR: %s\n", msg);
  exit(1);
}

/* Helper to safely get a string representation from a node 
   without allocating new memory. returns "" if unknown. */
static const char* get_node_val(librdf_node* node) {
    if (!node) return "";
    
    // CHANGE HERE: Use librdf_node_is_resource instead of librdf_node_is_uri
    if (librdf_node_is_resource(node)) {
        return (const char*)librdf_uri_as_string(librdf_node_get_uri(node));
    } 
    else if (librdf_node_is_literal(node)) {
        return (const char*)librdf_node_get_literal_value(node);
    } 
    else if (librdf_node_is_blank(node)) {
        return (const char*)librdf_node_get_blank_identifier(node);
    }
    
    return "";
}

static char* brick_uri(const char* cls) {
  // If already looks like a URI, keep it
  if (strstr(cls, "http://") == cls || strstr(cls, "https://") == cls) {
    return strdup(cls);
  }
  size_t n = strlen(BRICK_NS) + strlen(cls) + 1;
  char* out = (char*)malloc(n);
  snprintf(out, n, "%s%s", BRICK_NS, cls);
  return out;
}

int main(int argc, char** argv) {
  if (argc < 4) {
    fprintf(stderr, "Usage: %s <typed.ttl> <device_instance> <Class1,Class2,...>\n", argv[0]);
    return 2;
  }

  const char* ttl_path = argv[1];
  const int device_instance = atoi(argv[2]);
  const char* class_csv = argv[3];

  librdf_world* world = librdf_new_world();
  librdf_world_open(world);

  librdf_storage* storage = librdf_new_storage(world, "memory", NULL, NULL);
  if (!storage) die("failed to create storage");

  librdf_model* model = librdf_new_model(world, storage, NULL);
  if (!model) die("failed to create model");

  librdf_parser* parser = librdf_new_parser(world, "turtle", NULL, NULL);
  if (!parser) die("failed to create turtle parser");

  librdf_uri* base_uri = librdf_new_uri(world, (const unsigned char*)"urn:base:");
  librdf_uri* file_uri = librdf_new_uri_from_filename(world, ttl_path);

  if (librdf_parser_parse_into_model(parser, file_uri, base_uri, model) != 0) {
    die("failed to parse TTL");
  }

  // Build VALUES list: (<uri1>) (<uri2>) ...
  char* classes = strdup(class_csv);
  char* tok = strtok(classes, ",");
  char values_buf[8192];
  values_buf[0] = '\0';

  while (tok) {
    while (*tok == ' ') tok++;
    char* uri = brick_uri(tok);

    strcat(values_buf, "(<");
    strcat(values_buf, uri);
    strcat(values_buf, ">) ");

    free(uri);
    tok = strtok(NULL, ",");
  }
  free(classes);

  char query[16384];
  snprintf(query, sizeof(query),
    "PREFIX brick: <https://brickschema.org/schema/Brick#>\n"
    "PREFIX ref: <https://brickschema.org/schema/Brick/ref#>\n"
    "PREFIX bacnet: <http://data.ashrae.org/bacnet/2020#>\n"
    "PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>\n"
    "SELECT ?cls ?objId ?label\n"
    "WHERE {\n"
    "  ?point a ?cls .\n"
    "  OPTIONAL { ?point rdfs:label ?label . }\n"
    "  ?point ref:hasExternalReference ?ref .\n"
    "  ?ref bacnet:object-identifier ?objId .\n"
    "  ?ref bacnet:objectOf ?dev .\n"
    "  ?devRef bacnet:objectOf ?dev .\n"
    "  ?devRef bacnet:object-identifier \"device,%d\" .\n"
    "  VALUES (?cls) { %s }\n"
    "}\n",
    device_instance, values_buf
  );

  librdf_query* q = librdf_new_query(world, "sparql", NULL,
    (const unsigned char*)query, NULL);
  if (!q) die("failed to compile SPARQL query");

  librdf_query_results* results = librdf_query_execute(q, model);
  if (!results) die("failed to execute query");

  while (!librdf_query_results_finished(results)) {
    librdf_node* cls = librdf_query_results_get_binding_value(results, 0);
    librdf_node* obj = librdf_query_results_get_binding_value(results, 1);
    librdf_node* lab = librdf_query_results_get_binding_value(results, 2);

    // Use the helper function instead of librdf_node_to_string
    const char* cls_s = get_node_val(cls);
    const char* obj_s = get_node_val(obj);
    const char* lab_s = get_node_val(lab);

    printf("class=%s object=%s label=%s\n", cls_s, obj_s, lab_s);

    if (cls) librdf_free_node(cls);
    if (obj) librdf_free_node(obj);
    if (lab) librdf_free_node(lab);

    librdf_query_results_next(results);
  }


  librdf_free_query_results(results);
  librdf_free_query(q);

  librdf_free_uri(file_uri);
  librdf_free_uri(base_uri);
  librdf_free_parser(parser);
  librdf_free_model(model);
  librdf_free_storage(storage);
  librdf_free_world(world);

  return 0;
}
