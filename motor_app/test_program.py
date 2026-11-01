import unittest
from motion import make_step, compile_program


class ProgramTests(unittest.TestCase):
    def test_axes_run_in_parallel_but_keep_their_own_order(self):
        program = {'A': [make_step('A', 'CW', 1, 'speed', 1),
                         make_step('A', 'CCW', 1, 'time', 2)],
                   'B': [make_step('B', 'CW', 2, 'time', 4)]}
        commands, count, duration = compile_program(program)
        self.assertEqual(commands, [('CLEAR', 'ACK CLEAR'),
            ('ADD A 2500 200', 'ACK ADD A 1'),
            ('ADD A -2500 400', 'ACK ADD A 2'),
            ('ADD B 5000 400', 'ACK ADD B 1')])
        self.assertEqual(count, 3)
        self.assertEqual(duration, 4)  # max(A=3s, B=4s), not their sum

    def test_combined_scheduler_budget(self):
        program = {axis: [make_step(axis, 'CW', 1, 'speed', 5)] for axis in 'ABCD'}
        self.assertEqual(compile_program(program)[1], 4)
        program['A'] = [make_step('A', 'CW', 1, 'speed', 10)]
        with self.assertRaises(ValueError):
            compile_program(program)

    def test_empty_and_step_count_limit(self):
        with self.assertRaises(ValueError):
            compile_program({})
        step = make_step('A', 'CW', 0.1, 'time', 1)
        self.assertEqual(compile_program({'A': [step] * 32})[1], 32)
        with self.assertRaises(ValueError):
            compile_program({'A': [step] * 33})

    def test_wrong_axis_is_rejected(self):
        with self.assertRaises(ValueError):
            compile_program({'B': [make_step('A', 'CW', 1, 'speed', 1)]})


if __name__ == '__main__':
    unittest.main()
