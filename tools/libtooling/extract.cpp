// Source CFGs only. No whole-program resolution, execution or stable-ID claims.
#include "clang/AST/ASTConsumer.h"
#include "clang/AST/ParentMapContext.h"
#include "clang/AST/RecursiveASTVisitor.h"
#include "clang/Analysis/CFG.h"
#include "clang/Frontend/CompilerInstance.h"
#include "clang/Frontend/FrontendActions.h"
#include "clang/Frontend/TextDiagnosticPrinter.h"
#include "clang/Index/USRGeneration.h"
#include "clang/Lex/Lexer.h"
#include "clang/Tooling/CommonOptionsParser.h"
#include "clang/Tooling/Tooling.h"
#include "clang/Basic/Version.h"
#include "llvm/Support/CommandLine.h"
#include "llvm/Support/JSON.h"
#include "llvm/Support/FormatVariadic.h"
#include "llvm/Support/raw_ostream.h"
#include <map>
#include <memory>
#include <set>
#include <string>
using namespace clang;
using namespace clang::tooling;
namespace j = llvm::json;
static llvm::cl::OptionCategory Category("OnionTrace source extraction");
static llvm::cl::opt<std::string> Compilation("compilation-id", llvm::cl::Required, llvm::cl::cat(Category));
static llvm::cl::opt<std::string> Output("output", llvm::cl::Required, llvm::cl::cat(Category));

class Extractor : public RecursiveASTVisitor<Extractor> {
  ASTContext &Ctx;
  SourceManager &SM;
  PrintingPolicy Policy;
  j::Array &Functions;
  std::set<const ParmVarDecl *> MutableParameters;
  std::string usr(const Decl *D) {
    llvm::SmallString<128> U;
    if (index::generateUSRForDecl(D, U)) return "";
    return U.str().str();
  }
  std::string fid(const FunctionDecl *D) { return Compilation.getValue() + "/function/" + usr(D); }
  j::Value point(SourceLocation L) {
    if (L.isInvalid()) return nullptr;
    L = SM.getFileLoc(L);
    if (L.isInvalid() || !L.isFileID()) return nullptr;
    auto P = SM.getPresumedLoc(L);
    // Physical filenames/offsets are used; #line affects display only.
    return j::Object{{"path", SM.getFilename(L).str()}, {"offset", SM.getFileOffset(L)},
                     {"line", SM.getSpellingLineNumber(L)}, {"column", SM.getSpellingColumnNumber(L)},
                     {"presumed_path", P.isValid() ? std::string(P.getFilename()) : std::string()}};
  }
  j::Value span(SourceRange R, bool Spelling) {
    if (R.isInvalid()) return nullptr;
    auto Expansion = SM.getExpansionRange(R);
    auto B = Spelling ? SM.getSpellingLoc(R.getBegin()) : Expansion.getBegin();
    auto E = Spelling ? SM.getSpellingLoc(R.getEnd()) : Expansion.getEnd();
    auto End = (!Spelling && !Expansion.isTokenRange()) ? E :
      Lexer::getLocForEndOfToken(E, 0, SM, Ctx.getLangOpts());
    return j::Object{{"begin", point(B)}, {"end", point(End)}, {"end_inclusive", point(E)}};
  }
  std::string pretty(const Stmt *S) {
    std::string T;
    llvm::raw_string_ostream O(T);
    if (S) S->printPretty(O, nullptr, Policy);
    return T;
  }
  std::string linkage(const FunctionDecl *D) {
    if (D->getFormalLinkage() == Linkage::Internal) return "internal";
    if (D->getFormalLinkage() == Linkage::External) return "external";
    return "none";
  }
public:
  Extractor(ASTContext &C, j::Array &F) : Ctx(C), SM(C.getSourceManager()), Policy(C.getLangOpts()), Functions(F) {}
  j::Value range(SourceRange R) {
    return j::Object{{"expansion", span(R, false)}, {"spelling", span(R, true)},
                     {"macro", R.getBegin().isMacroID() || R.getEnd().isMacroID()}};
  }
  j::Object statement(const Stmt *S) {
    j::Object V{{"kind", S->getStmtClassName()}, {"text", pretty(S)}, {"range", range(S->getSourceRange())}};
    if (const auto *E = dyn_cast<Expr>(S)) V["type"] = E->getType().getAsString(Policy);
    if (const auto *D = dyn_cast<DeclRefExpr>(S)) {
      V["referenced_symbol"] = D->getDecl()->getNameAsString();
      V["referenced_usr"] = usr(D->getDecl());
    }
    return V;
  }
  // Deliberately small value domain: parameters and function designators can be
  // substituted across a direct call. Variables, casts and computed pointers
  // stay opaque; collecting candidate designators does not resolve them.
  j::Object value(const Expr *Input, const FunctionDecl *Owner) {
    const Expr *A = Input->IgnoreParenImpCasts();
    if (const auto *U = dyn_cast<UnaryOperator>(A))
      if (U->getOpcode() == UO_AddrOf) A = U->getSubExpr()->IgnoreParenImpCasts();
    j::Object V = statement(Input->IgnoreParenImpCasts());
    V["value_kind"] = "opaque";
    if (const auto *R = dyn_cast<DeclRefExpr>(A)) {
      if (const auto *F = dyn_cast<FunctionDecl>(R->getDecl())) {
        V["value_kind"] = "function";
        V["function_usr"] = usr(F);
        V["function_linkage"] = linkage(F);
        V["function_type"] = F->getType().getCanonicalType().getAsString(Policy);
      } else if (const auto *P = dyn_cast<ParmVarDecl>(R->getDecl()); P && !MutableParameters.count(P)) {
        for (unsigned I = 0; I < Owner->getNumParams(); ++I) if (Owner->getParamDecl(I) == P) {
          V["value_kind"] = "parameter";
          V["parameter_index"] = I;
        }
      }
    }
    if (const auto *C = dyn_cast<ConditionalOperator>(A)) {
      j::Array Candidates;
      Candidates.push_back(value(C->getTrueExpr(), Owner));
      Candidates.push_back(value(C->getFalseExpr(), Owner));
      V["candidates"] = std::move(Candidates);
    }
    return V;
  }
  j::Array guards(const CallExpr *Call) {
    j::Array Result;
    DynTypedNode Node = DynTypedNode::create(*Call);
    // Lexical guards are source evidence only, never a feasibility proof.
    for (unsigned Depth = 0; Depth < 256; ++Depth) {
      auto Parents = Ctx.getParents(Node);
      if (Parents.size() != 1) break;
      const Stmt *Child = Node.get<Stmt>();
      Node = Parents[0];
      const Stmt *Parent = Node.get<Stmt>();
      // Initializers pass through VarDecl before their DeclStmt. Continue
      // through that declaration so enclosing branch guards are not lost.
      if (!Parent) {
        if (Node.get<FunctionDecl>()) break;
        continue;
      }
      const Expr *Condition = nullptr;
      std::string Branch;
      if (const auto *I = dyn_cast<IfStmt>(Parent)) {
        if (Child == I->getThen()) { Condition = I->getCond(); Branch = "then"; }
        if (Child == I->getElse()) { Condition = I->getCond(); Branch = "else"; }
      } else if (const auto *F = dyn_cast<ForStmt>(Parent)) {
        if (Child == F->getBody()) { Condition = F->getCond(); Branch = "loop_body"; }
      } else if (const auto *W = dyn_cast<WhileStmt>(Parent)) {
        if (Child == W->getBody()) { Condition = W->getCond(); Branch = "loop_body"; }
      } else if (const auto *D = dyn_cast<DoStmt>(Parent)) {
        if (Child == D->getBody()) { Condition = D->getCond(); Branch = "do_body_first_iteration_unconditional"; }
      } else if (const auto *B = dyn_cast<BinaryOperator>(Parent)) {
        if (Child == B->getRHS() && B->isLogicalOp()) {
          Condition = B->getLHS(); Branch = B->getOpcode() == BO_LAnd ? "rhs_if_true" : "rhs_if_false";
        }
      } else if (const auto *C = dyn_cast<ConditionalOperator>(Parent)) {
        if (Child == C->getTrueExpr()) { Condition = C->getCond(); Branch = "then"; }
        if (Child == C->getFalseExpr()) { Condition = C->getCond(); Branch = "else"; }
      } else if (isa<SwitchStmt>(Parent) || isa<SwitchCase>(Parent)) {
        Result.push_back(j::Object{{"branch", "switch_context"}, {"context", statement(Parent)}});
      }
      if (!Branch.empty()) Result.push_back(j::Object{{"branch", Branch},
        {"condition", Condition ? j::Value(statement(Condition)) : j::Value(nullptr)}});
    }
    return Result;
  }
  j::Value threadApi(const FunctionDecl *F) {
    if (!F || linkage(F) != "external") return nullptr;
    std::string Name = F->getNameAsString();
    bool Posix = Name == "pthread_create";
    if ((!Posix && Name != "threadCreate") || usr(F) != "c:@F@" + Name ||
        F->getNumParams() != (Posix ? 4u : 6u)) return nullptr;
    QualType Entry = F->getParamDecl(Posix ? 2 : 1)->getType().getCanonicalType();
    if (!Entry->isPointerType()) return nullptr;
    const auto *Prototype = Entry->getPointeeType()->getAs<FunctionProtoType>();
    auto VoidPointer = [](QualType T) {
      return T->isPointerType() && T->getPointeeType()->isVoidType();
    };
    if (!Prototype || Prototype->getNumParams() != 1 ||
        !VoidPointer(Prototype->getReturnType()) || !VoidPointer(Prototype->getParamType(0)) ||
        !F->getParamDecl(0)->getType()->isPointerType() ||
        !VoidPointer(F->getParamDecl(Posix ? 3 : 2)->getType())) return nullptr;
    if (Posix ? !F->getReturnType()->isIntegerType() : !F->getReturnType()->isVoidType()) return nullptr;
    return j::Value(Name);
  }
  bool VisitFunctionDecl(FunctionDecl *D) {
    if (!D->doesThisDeclarationHaveABody() || D->isImplicit() || SM.isInSystemHeader(D->getLocation())) return true;
    // A definition appears once, including user-header inline definitions.
    if (D != D->getDefinition()) return true;
    MutableParameters.clear();
    class Writes : public RecursiveASTVisitor<Writes> {
      std::set<const ParmVarDecl *> &Modified;
      void mark(const Expr *E) {
        if (const auto *R = dyn_cast<DeclRefExpr>(E->IgnoreParenImpCasts()))
          if (const auto *P = dyn_cast<ParmVarDecl>(R->getDecl())) Modified.insert(P);
      }
    public:
      explicit Writes(std::set<const ParmVarDecl *> &M) : Modified(M) {}
      bool VisitBinaryOperator(BinaryOperator *B) {
        if (B->isAssignmentOp()) mark(B->getLHS());
        return true;
      }
      bool VisitUnaryOperator(UnaryOperator *U) {
        if (U->isIncrementDecrementOp() || U->getOpcode() == UO_AddrOf) mark(U->getSubExpr());
        return true;
      }
    } Writer(MutableParameters);
    Writer.TraverseStmt(D->getBody());
    std::string ID = fid(D);
    j::Array Blocks, Edges, Calls, Problems;
    if (Ctx.getLangOpts().CPlusPlus)
      Problems.push_back(j::Object{{"kind", "cxx_semantics_not_complete"},
                                  {"affects_completeness", true}});
    std::map<const Stmt *, std::string> Placement;
    CFG::BuildOptions Options;
    Options.setAllAlwaysAdd(); // Same expression granularity as debug.DumpCFG.
    auto Graph = CFG::buildCFG(D, D->getBody(), &Ctx, Options);
    if (!Graph) Problems.push_back(j::Object{{"kind", "cfg_build_failed"}, {"affects_completeness", true}});
    auto bid = [&](const CFGBlock *B) { return ID + "/B" + std::to_string(B->getBlockID()); };
    if (Graph) for (const CFGBlock *B : *Graph) {
      j::Array Elements, Successors;
      unsigned N = 0;
      for (const CFGElement &Element : *B) {
        if (auto S = Element.getAs<CFGStmt>()) {
          auto V = statement(S->getStmt());
          V["index"] = ++N;
          Placement[S->getStmt()] = bid(B);
          Elements.push_back(std::move(V));
        } else {
          Elements.push_back(j::Object{{"index", ++N}, {"kind", "unsupported_cfg_element"},
                                      {"clang_kind", static_cast<int>(Element.getKind())}});
          Problems.push_back(j::Object{{"kind", "unsupported_cfg_element"}, {"block_id", bid(B)},
                                      {"affects_completeness", true}});
        }
      }
      unsigned Ordinal = 0;
      for (auto I = B->succ_begin(); I != B->succ_end(); ++I, ++Ordinal) {
        const CFGBlock *Reachable = I->getReachableBlock();
        const CFGBlock *Possible = I->getPossiblyUnreachableBlock();
        std::string Kind = Reachable ? "normal" : (Possible ? "unreachable" : "unknown");
        const CFGBlock *Target = Reachable ? Reachable : Possible;
        Successors.push_back(j::Object{{"ordinal", Ordinal}, {"kind", Kind},
                                      {"target", Target ? j::Value(bid(Target)) : j::Value(nullptr)}});
        if (Reachable) Edges.push_back(j::Object{{"source", bid(B)}, {"target", bid(Reachable)}, {"ordinal", Ordinal}});
        else Problems.push_back(j::Object{{"kind", Kind + "_successor"}, {"source", bid(B)},
                                         {"affects_completeness", Kind != "unreachable"}});
      }
      j::Object V{{"id", bid(B)}, {"clang_id", "B" + std::to_string(B->getBlockID())},
                  {"entry", B == &Graph->getEntry()}, {"exit", B == &Graph->getExit()},
                  {"elements", std::move(Elements)}, {"successors", std::move(Successors)},
                  {"terminator", nullptr}, {"condition", nullptr}, {"loop_target", nullptr}};
      if (const Stmt *T = B->getTerminatorStmt()) V["terminator"] = statement(T);
      if (const Stmt *C = B->getTerminatorCondition()) V["condition"] = statement(C);
      if (const Stmt *L = B->getLoopTarget()) V["loop_target"] = statement(L);
      Blocks.push_back(std::move(V));
    }
    // Calls are collected from the AST separately; a missing CFG placement is
    // explicit and never represented as an executed or reachable call edge.
    class BodyVisitor : public RecursiveASTVisitor<BodyVisitor> {
      Extractor &E;
      const FunctionDecl *Function;
      std::string Owner;
      std::map<const Stmt *, std::string> &Placement;
      j::Array &Calls, &Problems;
    public:
      BodyVisitor(Extractor &E, const FunctionDecl *F, std::string O, std::map<const Stmt *, std::string> &P, j::Array &C, j::Array &U)
          : E(E), Function(F), Owner(O), Placement(P), Calls(C), Problems(U) {}
      bool TraverseLambdaExpr(LambdaExpr *L) {
        Problems.push_back(j::Object{{"kind", "unsupported_lambda"}, {"range", E.range(L->getSourceRange())},
                                    {"affects_completeness", true}});
        return true;
      }
      bool VisitStmt(Stmt *S) {
        if (isa<AsmStmt>(S) || isa<IndirectGotoStmt>(S) || isa<CoroutineBodyStmt>(S) ||
            isa<CXXConstructExpr>(S) || isa<CXXNewExpr>(S) || isa<CXXDeleteExpr>(S))
          Problems.push_back(j::Object{{"kind", S->getStmtClassName()}, {"range", E.range(S->getSourceRange())},
                                      {"affects_completeness", true}});
        return true;
      }
      bool VisitCallExpr(CallExpr *C) {
        const FunctionDecl *Callee = C->getDirectCallee();
        if (const auto *Method = dyn_cast_or_null<CXXMethodDecl>(Callee))
          if (Method->isVirtual()) Callee = nullptr; // Dynamic dispatch is unresolved.
        const FunctionDecl *Definition = Callee ? Callee->getDefinition() : nullptr;
        bool Local = Definition && !E.SM.isInSystemHeader(Definition->getLocation());
        j::Array Arguments;
        for (const Expr *A : C->arguments()) Arguments.push_back(E.value(A, Function));
        auto P = Placement.find(C);
        Calls.push_back(j::Object{
          {"id", Owner + "/call/" + std::to_string(Calls.size())}, {"caller_id", Owner},
          {"kind", Callee ? "direct" : "indirect"}, {"range", E.range(C->getSourceRange())},
          {"type", C->getType().getAsString(E.Policy)}, {"callee_type", C->getCallee()->getType().getAsString(E.Policy)},
          {"callee_expression", E.pretty(C->getCallee())}, {"arguments", std::move(Arguments)},
          {"conditions", E.guards(C)},
          {"thread_api", E.threadApi(Callee)},
          {"callee_canonical_type", Callee ? j::Value(Callee->getType().getCanonicalType().getAsString(E.Policy)) : j::Value(nullptr)},
          {"block_id", P == Placement.end() ? j::Value(nullptr) : j::Value(P->second)},
          {"placement_reason", P == Placement.end() ? j::Value("call not represented as a CFG statement") : j::Value(nullptr)},
          {"callee_usr", Callee ? j::Value(E.usr(Callee)) : j::Value(nullptr)},
          {"callee_name", Callee ? j::Value(Callee->getQualifiedNameAsString()) : j::Value(nullptr)},
          {"callee_linkage", Callee ? j::Value(E.linkage(Callee)) : j::Value(nullptr)},
          {"target_id", Local ? j::Value(E.fid(Definition)) : j::Value(nullptr)},
          {"resolution", Local ? "definition_in_tu" : "unresolved"},
          {"reason", Local ? j::Value(nullptr) : j::Value(Callee ? "definition not emitted in this TU; cross-TU resolution required" : "indirect call; points-to analysis not performed")}});
        return true;
      }
    } Visitor(*this, D, ID, Placement, Calls, Problems);
    Visitor.TraverseStmt(D->getBody());
    Functions.push_back(j::Object{{"id", ID}, {"usr", usr(D)}, {"name", D->getNameAsString()},
                                  {"qualified_name", D->getQualifiedNameAsString()}, {"type", D->getType().getAsString(Policy)},
                                  {"canonical_type", D->getType().getCanonicalType().getAsString(Policy)},
                                  {"parameter_count", D->getNumParams()},
                                  {"linkage", linkage(D)}, {"range", range(D->getSourceRange())},
                                  {"blocks", std::move(Blocks)}, {"edges", std::move(Edges)},
                                  {"calls", std::move(Calls)}, {"uncertainties", std::move(Problems)}});
    return true;
  }
};
class Consumer : public ASTConsumer {
  j::Array &Functions;
public:
  explicit Consumer(j::Array &F) : Functions(F) {}
  void HandleTranslationUnit(ASTContext &C) override { Extractor(C, Functions).TraverseDecl(C.getTranslationUnitDecl()); }
};
class Action : public ASTFrontendAction {
  j::Array &Functions;
public:
  explicit Action(j::Array &F) : Functions(F) {}
  std::unique_ptr<ASTConsumer> CreateASTConsumer(CompilerInstance &, llvm::StringRef) override {
    return std::make_unique<Consumer>(Functions);
  }
};
class Factory : public FrontendActionFactory {
  j::Array &Functions;
public:
  explicit Factory(j::Array &F) : Functions(F) {}
  std::unique_ptr<FrontendAction> create() override { return std::make_unique<Action>(Functions); }
};
class Diagnostics : public TextDiagnosticPrinter {
public:
  bool Failed = false;
  explicit Diagnostics(DiagnosticOptions *Options) : TextDiagnosticPrinter(llvm::errs(), Options) {}
  void HandleDiagnostic(DiagnosticsEngine::Level Level, const Diagnostic &Info) override {
    if (Level >= DiagnosticsEngine::Error) Failed = true;
    TextDiagnosticPrinter::HandleDiagnostic(Level, Info);
  }
};
int main(int argc, const char **argv) {
  auto Parser = CommonOptionsParser::create(argc, argv, Category, llvm::cl::OneOrMore);
  if (!Parser) { llvm::errs() << Parser.takeError(); return 2; }
  if (Parser->getSourcePathList().size() != 1) { llvm::errs() << "exactly one primary input required\n"; return 2; }
  j::Array Functions;
  ClangTool Tool(Parser->getCompilations(), Parser->getSourcePathList());
  llvm::IntrusiveRefCntPtr<DiagnosticOptions> DiagOptions(new DiagnosticOptions);
  Diagnostics Diag(DiagOptions.get());
  Tool.setDiagnosticConsumer(&Diag);
  Factory F(Functions);
  int Status = Tool.run(&F);
  // Driver diagnostics can report an error while still producing a frontend
  // job. Tool.run alone does not propagate all such failures.
  if (Diag.Failed) Status = 1;
  j::Object Result{{"schema_version", 1}, {"kind", "clang_source_extraction"},
                   {"compilation_id", Compilation.getValue()}, {"clang_version", getClangFullVersion()},
                   {"complete", Status == 0}, {"functions", std::move(Functions)},
                   {"cfg_policy", "Clang 18 CFG::BuildOptions defaults with setAllAlwaysAdd; unsupported implicit elements reported"},
                   {"limitations", j::Array{"source CFG, not optimized instructions or path feasibility", "USR requires linkage and build/TU scope; not cross-build stable", "indirect and cross-TU calls remain unresolved", "system-header definitions omitted; calls retained", "no runtime observations"}}};
  std::error_code EC;
  llvm::raw_fd_ostream OS(Output.getValue(), EC);
  if (EC) { llvm::errs() << EC.message(); return 2; }
  OS << llvm::formatv("{0:2}\n", j::Value(std::move(Result)));
  return Status;
}
