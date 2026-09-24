// Package publishcheck asserts the order of the gates in PublishDocker from
// the module's syntax tree. It imports nothing from the module, so it runs
// without the generated SDK (`go test ./publishcheck/`), and it reads calls,
// not text: a call that is commented out is not in the tree, and renaming a
// local variable is not a change of behaviour.
package publishcheck

import (
	"go/ast"
	"go/parser"
	"go/token"
	"slices"
	"strconv"
	"testing"
)

// function returns the method of *HtrflowBatch (or plain function) called
// name in ../<file>.
func function(t *testing.T, file, name string) *ast.FuncDecl {
	t.Helper()
	parsed, err := parser.ParseFile(token.NewFileSet(), "../"+file, nil, 0)
	if err != nil {
		t.Fatalf("parse %s: %v", file, err)
	}
	for _, decl := range parsed.Decls {
		if fn, ok := decl.(*ast.FuncDecl); ok && fn.Name.Name == name {
			return fn
		}
	}
	t.Fatalf("%s: no func %s", file, name)
	return nil
}

// calls lists the calls in body in source order.
func calls(body ast.Node) []*ast.CallExpr {
	var found []*ast.CallExpr
	ast.Inspect(body, func(n ast.Node) bool {
		if call, ok := n.(*ast.CallExpr); ok {
			found = append(found, call)
		}
		return true
	})
	return found
}

// method is the selector name of a call x.Name(...), and the root identifier
// of x ("m" for m.Test, "container" for container.WithRegistryAuth(...).Publish).
func method(call *ast.CallExpr) (name, root string) {
	sel, ok := call.Fun.(*ast.SelectorExpr)
	if !ok {
		return "", ""
	}
	x := sel.X
	for {
		switch v := x.(type) {
		case *ast.CallExpr:
			x = v.Fun
			continue
		case *ast.SelectorExpr:
			x = v.X
			continue
		case *ast.Ident:
			return sel.Sel.Name, v.Name
		}
		return sel.Sel.Name, ""
	}
}

func ident(e ast.Expr) string {
	if id, ok := e.(*ast.Ident); ok {
		return id.Name
	}
	return ""
}

func str(e ast.Expr) string {
	if lit, ok := e.(*ast.BasicLit); ok && lit.Kind == token.STRING {
		s, _ := strconv.Unquote(lit.Value)
		return s
	}
	return ""
}

// The gates PublishDocker runs, by the method that runs them.
var gates = map[string]string{
	"refuseExistingTags": "refuse",
	"Test":               "test",
	"BuildWrapper":       "build",
	"BuildWeb":           "build",
	"BuildCampaigns":     "build",
	"driverTest":         "driver-test",
	"scanImage":          "scan",
	"Publish":            "publish",
}

func TestPublishDockerGatesThePushInOrder(t *testing.T) {
	// Finding 3069: the registry is asked before the tests and again right
	// before the push. Finding 3104: the level-0 driver test runs on the
	// container that is pushed. Finding 3060: so does the CRITICAL scan.
	fn := function(t, "publish.go", "PublishDocker")
	var order []string
	byGate := map[string][]*ast.CallExpr{}
	for _, call := range calls(fn.Body) {
		name, root := method(call)
		gate, ok := gates[name]
		if !ok || (gate != "publish" && root != "m") {
			continue
		}
		byGate[gate] = append(byGate[gate], call)
		// one gate per switch case or return branch counts once
		if len(order) == 0 || order[len(order)-1] != gate {
			order = append(order, gate)
		}
	}
	want := []string{"refuse", "test", "build", "driver-test", "scan", "refuse", "publish"}
	if !slices.Equal(order, want) {
		t.Fatalf("PublishDocker runs %v, want %v", order, want)
	}

	// The refs asked about are the refs pushed, and the container built is
	// the one tested, scanned and pushed -- whatever the variables are called.
	refs := ident(byGate["refuse"][0].Args[2])
	for _, call := range byGate["refuse"] {
		if ident(call.Args[2]) != refs || refs == "" {
			t.Errorf("the two registry checks ask about different refs")
		}
	}
	pushed, built := "", map[string]bool{}
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		as, ok := n.(*ast.AssignStmt)
		if !ok || len(as.Rhs) != 1 {
			return true
		}
		if ix, ok := as.Rhs[0].(*ast.IndexExpr); ok && ident(ix.X) == refs {
			if lit, _ := ix.Index.(*ast.BasicLit); lit != nil && lit.Value == "0" {
				pushed = ident(as.Lhs[0])
			}
		}
		if call, ok := as.Rhs[0].(*ast.CallExpr); ok && slices.Contains(byGate["build"], call) {
			built[ident(as.Lhs[0])] = true
		}
		return true
	})
	if pushed == "" || len(built) != 1 {
		t.Fatalf("want one ref taken from %s[0] and one container built, got %q and %v", refs, pushed, built)
	}
	for _, call := range byGate["publish"] {
		if _, root := method(call); !built[root] || ident(call.Args[1]) != pushed {
			t.Errorf("Publish pushes %s as %s, not the built container as %s", root, ident(call.Args[1]), pushed)
		}
	}
	for _, call := range byGate["driver-test"] {
		if !built[ident(call.Args[1])] {
			t.Errorf("driverTest runs on %s, not the built container", ident(call.Args[1]))
		}
	}
	for _, call := range byGate["scan"] {
		if !built[ident(call.Args[1])] || str(call.Args[2]) != "CRITICAL" {
			t.Errorf("scanImage gates %s at %q, not the built container at CRITICAL", ident(call.Args[1]), str(call.Args[2]))
		}
	}
}

func TestOnlyAnUnknownManifestCountsAsAFreeTag(t *testing.T) {
	// A registry that cannot be asked must refuse, not pass: the one
	// (false, nil) return sits under the MANIFEST_UNKNOWN/NAME_UNKNOWN test,
	// and the function ends in an error.
	fn := function(t, "publish.go", "tagExists")
	var free []*ast.IfStmt
	ast.Inspect(fn.Body, func(n ast.Node) bool {
		stmt, ok := n.(*ast.IfStmt)
		if !ok {
			return true
		}
		for _, s := range stmt.Body.List {
			if ret, ok := s.(*ast.ReturnStmt); ok && len(ret.Results) == 2 &&
				ident(ret.Results[0]) == "false" && ident(ret.Results[1]) == "nil" {
				free = append(free, stmt)
			}
		}
		return true
	})
	if len(free) != 1 {
		t.Fatalf("tagExists has %d (false, nil) returns, want 1", len(free))
	}
	var named []string
	ast.Inspect(free[0].Cond, func(n ast.Node) bool {
		if e, ok := n.(ast.Expr); ok && str(e) != "" {
			named = append(named, str(e))
		}
		return true
	})
	slices.Sort(named)
	if !slices.Equal(named, []string{"MANIFEST_UNKNOWN", "NAME_UNKNOWN"}) {
		t.Errorf("a tag counts as free on %v", named)
	}
	last, ok := fn.Body.List[len(fn.Body.List)-1].(*ast.ReturnStmt)
	if !ok || ident(last.Results[1]) == "nil" {
		t.Error("tagExists does not end in an error for an answer it cannot read")
	}
}

func TestTheTransformersLineReachesTheWrapperBuild(t *testing.T) {
	publish := function(t, "publish.go", "PublishDocker")
	// transformersVersion is the public --transformers-version flag of both
	// functions, not a local name
	passed := false
	for _, call := range calls(publish.Body) {
		if name, root := method(call); name == "BuildWrapper" && root == "m" {
			passed = ident(call.Args[len(call.Args)-1]) == "transformersVersion"
		}
	}
	if !passed {
		t.Error("PublishDocker does not hand transformersVersion to BuildWrapper")
	}
	build := function(t, "build.go", "BuildWrapper")
	arg := false
	ast.Inspect(build.Body, func(n ast.Node) bool {
		lit, ok := n.(*ast.CompositeLit)
		if !ok {
			return true
		}
		fields := map[string]ast.Expr{}
		for _, el := range lit.Elts {
			if kv, ok := el.(*ast.KeyValueExpr); ok {
				fields[ident(kv.Key)] = kv.Value
			}
		}
		if str(fields["Name"]) == "TRANSFORMERS_VERSION" && ident(fields["Value"]) == "transformersVersion" {
			arg = true
		}
		return true
	})
	if !arg {
		t.Error("BuildWrapper does not pass transformersVersion as the TRANSFORMERS_VERSION build arg")
	}
}
