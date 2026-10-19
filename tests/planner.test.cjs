const assert = require('node:assert/strict');
const {summarize, minutesText} = require('../static/planner.js');
const day = '2026-10-19';
const entry = (start, end, extra={}) => ({day, kind:'task', done:0, priority:'normal',
  starts_at: start ? `${day}T${start}:00` : null,
  ends_at: end ? `${day}T${end}:00` : null, ...extra});
let s = summarize([entry('15:00','17:00'),entry('16:00','18:00')],day);
assert.equal(s.busy,180); assert.equal(s.overlap,60);
s = summarize([entry('15:00','17:00'),entry('15:30','16:00'),entry('16:00','18:00')],day);
assert.equal(s.busy,180); assert.equal(s.overlap,90);
s = summarize([entry('15:00','16:00'),entry('16:00','17:00',{done:1}),entry(null,null,{priority:'high'}),entry('12:00',null)],day);
assert.equal(s.busy,120); assert.equal(s.overlap,0); assert.equal(s.done,1);
assert.equal(s.important,1); assert.equal(s.untimed,2);
assert.equal(summarize([],day).busy,0);
assert.equal(summarize([entry('15:00','17:00',{kind:'note'}),entry('10:00','12:00',{day:'2026-10-20'})],day).busy,0);
assert.equal(minutesText(120),'2 ч'); assert.equal(minutesText(95),'1 ч 35 мин'); assert.equal(minutesText(0),'0 мин');
console.log('Planner: 16 assertions passed');
