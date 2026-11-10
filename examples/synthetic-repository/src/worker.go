package src

import "fmt"

type Worker struct {
	Name string
}

func (w Worker) Label() string {
	return fmt.Sprintf("worker:%s", w.Name)
}
