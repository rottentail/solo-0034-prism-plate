// Command verify is the offline, same-language lineage checker for site restore.
// It reuses the exact compilation, lineage and bundle code paths the service runs;
// visual equivalence is intentionally out of scope here (see site/verify_release.py).
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"flag"
	"fmt"
	"os"

	"prismplate/internal/bundle"
	"prismplate/internal/repository"
	"prismplate/internal/validate"
)

var failures int

func check(name string, ok bool, detail string) {
	if ok {
		fmt.Printf("[PASS] %s\n", name)
		return
	}
	failures++
	if detail != "" {
		fmt.Printf("[FAIL] %s — %s\n", name, detail)
	} else {
		fmt.Printf("[FAIL] %s\n", name)
	}
}

func main() {
	snapshot := flag.String("snapshot", "", "path to a restored state.json")
	bundlePath := flag.String("bundle", "", "path to an exported bundle JSON")
	manifest := flag.String("manifest", "", "optional release-manifest.json for cross-checking")
	flag.Parse()
	if *snapshot == "" && *bundlePath == "" {
		fmt.Fprintln(os.Stderr, "usage: verify [-snapshot state.json] [-bundle envelope.json] [-manifest release-manifest.json]")
		os.Exit(2)
	}

	var man struct {
		Signed struct {
			Plate    string `json:"plate"`
			Revision int    `json:"revision"`
			Proofs   []struct {
				ID             string `json:"id"`
				ArtifactSHA256 string `json:"artifact_sha256"`
				File           string `json:"file"`
				State          string `json:"state"`
			} `json:"proofs"`
		} `json:"signed_recovery"`
	}
	if *manifest != "" {
		raw, err := os.ReadFile(*manifest)
		if err != nil {
			fmt.Fprintln(os.Stderr, "manifest:", err)
			os.Exit(2)
		}
		if err := json.Unmarshal(raw, &man); err != nil {
			fmt.Fprintln(os.Stderr, "manifest decode:", err)
			os.Exit(2)
		}
	}

	if *snapshot != "" {
		raw, err := os.ReadFile(*snapshot)
		check("快照可读取", err == nil, errString(err))
		if err == nil {
			var st repository.State
			err = json.Unmarshal(raw, &st)
			check("快照 JSON 可解码", err == nil, errString(err))
			if err == nil {
				// 与 repository.Open、cmd 启动恢复完全相同的谱系与准入校验
				check("repository.Check 谱系完整", repository.Check(st) == nil, errString(repository.Check(st)))
				compileOK := true
				for _, p := range st.Plates {
					for _, r := range p.Revisions {
						if _, e := validate.Compile(r.Document); e != nil {
							compileOK = false
							fmt.Printf("[FAIL] 稿本 %s#%d 重新编译准入: %v\n", p.ID, r.Number, e)
							failures++
						}
					}
				}
				check("全部稿本通过统一编译准入", compileOK, "")
				// 已签成品：字节摘要与随包文件（若给了 manifest）
				want := map[string]string{}
				for _, pr := range man.Signed.Proofs {
					if pr.State == "accepted" {
						want[pr.ID] = pr.ArtifactSHA256
					}
				}
				for _, pr := range st.Proofs {
					if pr.State != "accepted" {
						continue
					}
					a := st.Artifacts[pr.Artifact]
					sum := sha256.Sum256(a.Data)
					digest := hex.EncodeToString(sum[:])
					check(fmt.Sprintf("已签 %s 成品摘要自洽且原样保留", pr.ID),
						digest == a.Digest && a.State == "available", digest[:16]+"…")
					if w, ok := want[pr.ID]; ok {
						check(fmt.Sprintf("已签 %s 与发布清单摘要一致", pr.ID), digest == w,
							"live="+digest[:16]+" manifest="+w[:16])
					}
				}
				if man.Signed.Plate != "" {
					p, ok := st.Plates[man.Signed.Plate]
					check("清单版面存在于恢复快照", ok, man.Signed.Plate)
					if ok && man.Signed.Revision >= 1 && man.Signed.Revision <= len(p.Revisions) {
						r := p.Revisions[man.Signed.Revision-1]
						check("清单稿本号与快照一致", r.Number == man.Signed.Revision, fmt.Sprintf("r%d", r.Number))
					}
				}
			}
		}
	}

	if *bundlePath != "" {
		raw, err := os.ReadFile(*bundlePath)
		check("封包可读取", err == nil, errString(err))
		if err == nil {
			// bundle.Open 执行 schema、内层摘要、谱系与逐稿本编译的全套拒绝路径
			p, err := bundle.Open(raw)
			check("bundle.Open 封包校验通过", err == nil, errString(err))
			if err == nil {
				check("封包版面含至少一个稿本", len(p.Revisions) >= 1, fmt.Sprintf("revisions=%d", len(p.Revisions)))
			}
		}
	}

	fmt.Printf("\n汇总：%d 项失败\n", failures)
	if failures > 0 {
		os.Exit(1)
	}
}

func errString(err error) string {
	if err == nil {
		return ""
	}
	return err.Error()
}
