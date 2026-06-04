function D(n, t, e, i) {
  function s(r) {
    return r instanceof e ? r : new e(function(o) {
      o(r);
    });
  }
  return new (e || (e = Promise))(function(r, o) {
    function a(d) {
      try {
        u(i.next(d));
      } catch (h) {
        o(h);
      }
    }
    function c(d) {
      try {
        u(i.throw(d));
      } catch (h) {
        o(h);
      }
    }
    function u(d) {
      d.done ? r(d.value) : s(d.value).then(a, c);
    }
    u((i = i.apply(n, t || [])).next());
  });
}
function is(n) {
  var t = typeof Symbol == "function" && Symbol.iterator, e = t && n[t], i = 0;
  if (e) return e.call(n);
  if (n && typeof n.length == "number") return {
    next: function() {
      return n && i >= n.length && (n = void 0), { value: n && n[i++], done: !n };
    }
  };
  throw new TypeError(t ? "Object is not iterable." : "Symbol.iterator is not defined.");
}
function F(n) {
  return this instanceof F ? (this.v = n, this) : new F(n);
}
function Dt(n, t, e) {
  if (!Symbol.asyncIterator) throw new TypeError("Symbol.asyncIterator is not defined.");
  var i = e.apply(n, t || []), s, r = [];
  return s = Object.create((typeof AsyncIterator == "function" ? AsyncIterator : Object).prototype), a("next"), a("throw"), a("return", o), s[Symbol.asyncIterator] = function() {
    return this;
  }, s;
  function o(O) {
    return function(j) {
      return Promise.resolve(j).then(O, h);
    };
  }
  function a(O, j) {
    i[O] && (s[O] = function(Jt) {
      return new Promise(function(qn, kt) {
        r.push([O, Jt, qn, kt]) > 1 || c(O, Jt);
      });
    }, j && (s[O] = j(s[O])));
  }
  function c(O, j) {
    try {
      u(i[O](j));
    } catch (Jt) {
      T(r[0][3], Jt);
    }
  }
  function u(O) {
    O.value instanceof F ? Promise.resolve(O.value.v).then(d, h) : T(r[0][2], O);
  }
  function d(O) {
    c("next", O);
  }
  function h(O) {
    c("throw", O);
  }
  function T(O, j) {
    O(j), r.shift(), r.length && c(r[0][0], r[0][1]);
  }
}
function ln(n) {
  var t, e;
  return t = {}, i("next"), i("throw", function(s) {
    throw s;
  }), i("return"), t[Symbol.iterator] = function() {
    return this;
  }, t;
  function i(s, r) {
    t[s] = n[s] ? function(o) {
      return (e = !e) ? { value: F(n[s](o)), done: !1 } : r ? r(o) : o;
    } : r;
  }
}
function pe(n) {
  if (!Symbol.asyncIterator) throw new TypeError("Symbol.asyncIterator is not defined.");
  var t = n[Symbol.asyncIterator], e;
  return t ? t.call(n) : (n = typeof is == "function" ? is(n) : n[Symbol.iterator](), e = {}, i("next"), i("throw"), i("return"), e[Symbol.asyncIterator] = function() {
    return this;
  }, e);
  function i(r) {
    e[r] = n[r] && function(o) {
      return new Promise(function(a, c) {
        o = n[r](o), s(a, c, o.done, o.value);
      });
    };
  }
  function s(r, o, a, c) {
    Promise.resolve(c).then(function(u) {
      r({ value: u, done: a });
    }, o);
  }
}
const ss = new TextDecoder("utf-8"), ri = ss.decode.bind(ss), ha = new TextEncoder(), Ze = (n) => ha.encode(n), fa = (n) => typeof n == "number", pa = (n) => typeof n == "boolean", G = (n) => typeof n == "function", gt = (n) => n != null && Object(n) === n, Pe = (n) => gt(n) && G(n.then), Pn = (n) => gt(n) && G(n[Symbol.iterator]), bi = (n) => gt(n) && G(n[Symbol.asyncIterator]), oi = (n) => gt(n) && gt(n.schema), xs = (n) => gt(n) && "done" in n && "value" in n, Cs = (n) => gt(n) && G(n.stat) && fa(n.fd), Es = (n) => gt(n) && _i(n.body), Vs = (n) => "_getDOMStream" in n && "_getNodeStream" in n, _i = (n) => gt(n) && G(n.cancel) && G(n.getReader) && !Vs(n), Rs = (n) => gt(n) && G(n.read) && G(n.pipe) && pa(n.readable) && !Vs(n), ya = (n) => gt(n) && G(n.clear) && G(n.bytes) && G(n.position) && G(n.setPosition) && G(n.capacity) && G(n.getBufferIdentifier) && G(n.createLong), vi = typeof SharedArrayBuffer < "u" ? SharedArrayBuffer : ArrayBuffer;
function ga(n) {
  const t = n[0] ? [n[0]] : [];
  let e, i, s, r;
  for (let o, a, c = 0, u = 0, d = n.length; ++c < d; ) {
    if (o = t[u], a = n[c], !o || !a || o.buffer !== a.buffer || a.byteOffset < o.byteOffset) {
      a && (t[++u] = a);
      continue;
    }
    if ({ byteOffset: e, byteLength: s } = o, { byteOffset: i, byteLength: r } = a, e + s < i || i + r < e) {
      a && (t[++u] = a);
      continue;
    }
    t[u] = new Uint8Array(o.buffer, e, i - e + r);
  }
  return t;
}
function ai(n, t, e = 0, i = t.byteLength) {
  const s = n.byteLength, r = new Uint8Array(n.buffer, n.byteOffset, s), o = new Uint8Array(t.buffer, t.byteOffset, Math.min(i, s));
  return r.set(o, e), n;
}
function Ot(n, t) {
  const e = ga(n), i = e.reduce((d, h) => d + h.byteLength, 0);
  let s, r, o, a = 0, c = -1;
  const u = Math.min(t || Number.POSITIVE_INFINITY, i);
  for (const d = e.length; ++c < d; ) {
    if (s = e[c], r = s.subarray(0, Math.min(s.length, u - a)), u <= a + r.length) {
      r.length < s.length ? e[c] = s.subarray(r.length) : r.length === s.length && c++, o ? ai(o, r, a) : o = r;
      break;
    }
    ai(o || (o = new Uint8Array(u)), r, a), a += r.length;
  }
  return [o || new Uint8Array(0), e.slice(c), i - (o ? o.byteLength : 0)];
}
function R(n, t) {
  let e = xs(t) ? t.value : t;
  return e instanceof n ? n === Uint8Array ? new n(e.buffer, e.byteOffset, e.byteLength) : e : e ? (typeof e == "string" && (e = Ze(e)), e instanceof ArrayBuffer ? new n(e) : e instanceof vi ? new n(e) : ya(e) ? R(n, e.bytes()) : ArrayBuffer.isView(e) ? e.byteLength <= 0 ? new n(0) : new n(e.buffer, e.byteOffset, e.byteLength / n.BYTES_PER_ELEMENT) : n.from(e)) : new n(0);
}
const Te = (n) => R(Int32Array, n), rs = (n) => R(BigInt64Array, n), N = (n) => R(Uint8Array, n), ci = (n) => (n.next(), n);
function* ma(n, t) {
  const e = function* (s) {
    yield s;
  }, i = typeof t == "string" || ArrayBuffer.isView(t) || t instanceof ArrayBuffer || t instanceof vi ? e(t) : Pn(t) ? t : e(t);
  return yield* ci((function* (s) {
    let r = null;
    do
      r = s.next(yield R(n, r));
    while (!r.done);
  })(i[Symbol.iterator]())), new n();
}
const ba = (n) => ma(Uint8Array, n);
function zs(n, t) {
  return Dt(this, arguments, function* () {
    if (Pe(t))
      return yield F(yield F(yield* ln(pe(zs(n, yield F(t))))));
    const i = function(o) {
      return Dt(this, arguments, function* () {
        yield yield F(yield F(o));
      });
    }, s = function(o) {
      return Dt(this, arguments, function* () {
        yield F(yield* ln(pe(ci((function* (a) {
          let c = null;
          do
            c = a.next(yield c?.value);
          while (!c.done);
        })(o[Symbol.iterator]())))));
      });
    }, r = typeof t == "string" || ArrayBuffer.isView(t) || t instanceof ArrayBuffer || t instanceof vi ? i(t) : Pn(t) ? s(t) : bi(t) ? t : i(t);
    return yield F(
      // otherwise if AsyncIterable, use it
      yield* ln(pe(ci((function(o) {
        return Dt(this, arguments, function* () {
          let a = null;
          do
            a = yield F(o.next(yield yield F(R(n, a))));
          while (!a.done);
        });
      })(r[Symbol.asyncIterator]()))))
    ), yield F(new n());
  });
}
const _a = (n) => zs(Uint8Array, n);
function va(n, t) {
  let e = 0;
  const i = n.length;
  if (i !== t.length)
    return !1;
  if (i > 0)
    do
      if (n[e] !== t[e])
        return !1;
    while (++e < i);
  return !0;
}
const ct = {
  fromIterable(n) {
    return on(wa(n));
  },
  fromAsyncIterable(n) {
    return on(Ia(n));
  },
  fromDOMStream(n) {
    return on(Sa(n));
  },
  fromNodeStream(n) {
    return on(Aa(n));
  },
  // @ts-ignore
  toDOMStream(n, t) {
    throw new Error('"toDOMStream" not available in this environment');
  },
  // @ts-ignore
  toNodeStream(n, t) {
    throw new Error('"toNodeStream" not available in this environment');
  }
}, on = (n) => (n.next(), n);
function* wa(n) {
  let t, e = !1, i = [], s, r, o, a = 0;
  function c() {
    return r === "peek" ? Ot(i, o)[0] : ([s, i, a] = Ot(i, o), s);
  }
  ({ cmd: r, size: o } = (yield null) || { cmd: "read", size: 0 });
  const u = ba(n)[Symbol.iterator]();
  try {
    do
      if ({ done: t, value: s } = Number.isNaN(o - a) ? u.next() : u.next(o - a), !t && s.byteLength > 0 && (i.push(s), a += s.byteLength), t || o <= a)
        do
          ({ cmd: r, size: o } = yield c());
        while (o < a);
    while (!t);
  } catch (d) {
    e = !0, typeof u.throw == "function" && u.throw(d);
  } finally {
    e === !1 && typeof u.return == "function" && u.return(null);
  }
  return null;
}
function Ia(n) {
  return Dt(this, arguments, function* () {
    let e, i = !1, s = [], r, o, a, c = 0;
    function u() {
      return o === "peek" ? Ot(s, a)[0] : ([r, s, c] = Ot(s, a), r);
    }
    ({ cmd: o, size: a } = (yield yield F(null)) || { cmd: "read", size: 0 });
    const d = _a(n)[Symbol.asyncIterator]();
    try {
      do
        if ({ done: e, value: r } = Number.isNaN(a - c) ? yield F(d.next()) : yield F(d.next(a - c)), !e && r.byteLength > 0 && (s.push(r), c += r.byteLength), e || a <= c)
          do
            ({ cmd: o, size: a } = yield yield F(u()));
          while (a < c);
      while (!e);
    } catch (h) {
      i = !0, typeof d.throw == "function" && (yield F(d.throw(h)));
    } finally {
      i === !1 && typeof d.return == "function" && (yield F(d.return(new Uint8Array(0))));
    }
    return yield F(null);
  });
}
function Sa(n) {
  return Dt(this, arguments, function* () {
    let e = !1, i = !1, s = [], r, o, a, c = 0;
    function u() {
      return o === "peek" ? Ot(s, a)[0] : ([r, s, c] = Ot(s, a), r);
    }
    ({ cmd: o, size: a } = (yield yield F(null)) || { cmd: "read", size: 0 });
    const d = new Ba(n);
    try {
      do
        if ({ done: e, value: r } = Number.isNaN(a - c) ? yield F(d.read()) : yield F(d.read(a - c)), !e && r.byteLength > 0 && (s.push(N(r)), c += r.byteLength), e || a <= c)
          do
            ({ cmd: o, size: a } = yield yield F(u()));
          while (a < c);
      while (!e);
    } catch (h) {
      i = !0, yield F(d.cancel(h));
    } finally {
      i === !1 ? yield F(d.cancel()) : n.locked && d.releaseLock();
    }
    return yield F(null);
  });
}
class Ba {
  constructor(t) {
    this.source = t, this.reader = null, this.reader = this.source.getReader(), this.reader.closed.catch(() => {
    });
  }
  get closed() {
    return this.reader ? this.reader.closed.catch(() => {
    }) : Promise.resolve();
  }
  releaseLock() {
    this.reader && this.reader.releaseLock(), this.reader = null;
  }
  cancel(t) {
    return D(this, void 0, void 0, function* () {
      const { reader: e, source: i } = this;
      e && (yield e.cancel(t).catch(() => {
      })), i && i.locked && this.releaseLock();
    });
  }
  read(t) {
    return D(this, void 0, void 0, function* () {
      if (t === 0)
        return { done: this.reader == null, value: new Uint8Array(0) };
      const e = yield this.reader.read();
      return !e.done && (e.value = N(e)), e;
    });
  }
}
const Zn = (n, t) => {
  const e = (s) => i([t, s]);
  let i;
  return [t, e, new Promise((s) => (i = s) && n.once(t, e))];
};
function Aa(n) {
  return Dt(this, arguments, function* () {
    const e = [];
    let i = "error", s = !1, r = null, o, a, c = 0, u = [], d;
    function h() {
      return o === "peek" ? Ot(u, a)[0] : ([d, u, c] = Ot(u, a), d);
    }
    if ({ cmd: o, size: a } = (yield yield F(null)) || { cmd: "read", size: 0 }, n.isTTY)
      return yield yield F(new Uint8Array(0)), yield F(null);
    try {
      e[0] = Zn(n, "end"), e[1] = Zn(n, "error");
      do {
        if (e[2] = Zn(n, "readable"), [i, r] = yield F(Promise.race(e.map((O) => O[2]))), i === "error")
          break;
        if ((s = i === "end") || (Number.isFinite(a - c) ? (d = N(n.read(a - c)), d.byteLength < a - c && (d = N(n.read()))) : d = N(n.read()), d.byteLength > 0 && (u.push(d), c += d.byteLength)), s || a <= c)
          do
            ({ cmd: o, size: a } = yield yield F(h()));
          while (a < c);
      } while (!s);
    } finally {
      yield F(T(e, i === "error" ? r : null));
    }
    return yield F(null);
    function T(O, j) {
      return d = u = null, new Promise((Jt, qn) => {
        for (const [kt, da] of O)
          n.off(kt, da);
        try {
          const kt = n.destroy;
          kt && kt.call(n, j), j = void 0;
        } catch (kt) {
          j = kt || j;
        } finally {
          j != null ? qn(j) : Jt();
        }
      });
    }
  });
}
var $;
(function(n) {
  n[n.V1 = 0] = "V1", n[n.V2 = 1] = "V2", n[n.V3 = 2] = "V3", n[n.V4 = 3] = "V4", n[n.V5 = 4] = "V5";
})($ || ($ = {}));
var X;
(function(n) {
  n[n.Sparse = 0] = "Sparse", n[n.Dense = 1] = "Dense";
})(X || (X = {}));
var W;
(function(n) {
  n[n.HALF = 0] = "HALF", n[n.SINGLE = 1] = "SINGLE", n[n.DOUBLE = 2] = "DOUBLE";
})(W || (W = {}));
var dt;
(function(n) {
  n[n.DAY = 0] = "DAY", n[n.MILLISECOND = 1] = "MILLISECOND";
})(dt || (dt = {}));
var b;
(function(n) {
  n[n.SECOND = 0] = "SECOND", n[n.MILLISECOND = 1] = "MILLISECOND", n[n.MICROSECOND = 2] = "MICROSECOND", n[n.NANOSECOND = 3] = "NANOSECOND";
})(b || (b = {}));
var J;
(function(n) {
  n[n.YEAR_MONTH = 0] = "YEAR_MONTH", n[n.DAY_TIME = 1] = "DAY_TIME", n[n.MONTH_DAY_NANO = 2] = "MONTH_DAY_NANO";
})(J || (J = {}));
const Qn = 2, St = 4, Tt = 4, E = 4, $t = new Int32Array(2), os = new Float32Array($t.buffer), as = new Float64Array($t.buffer), an = new Uint16Array(new Uint8Array([1, 0]).buffer)[0] === 1;
var li;
(function(n) {
  n[n.UTF8_BYTES = 1] = "UTF8_BYTES", n[n.UTF16_STRING = 2] = "UTF16_STRING";
})(li || (li = {}));
let Qt = class ks {
  /**
   * Create a new ByteBuffer with a given array of bytes (`Uint8Array`)
   */
  constructor(t) {
    this.bytes_ = t, this.position_ = 0, this.text_decoder_ = new TextDecoder();
  }
  /**
   * Create and allocate a new ByteBuffer with a given size.
   */
  static allocate(t) {
    return new ks(new Uint8Array(t));
  }
  clear() {
    this.position_ = 0;
  }
  /**
   * Get the underlying `Uint8Array`.
   */
  bytes() {
    return this.bytes_;
  }
  /**
   * Get the buffer's position.
   */
  position() {
    return this.position_;
  }
  /**
   * Set the buffer's position.
   */
  setPosition(t) {
    this.position_ = t;
  }
  /**
   * Get the buffer's capacity.
   */
  capacity() {
    return this.bytes_.length;
  }
  readInt8(t) {
    return this.readUint8(t) << 24 >> 24;
  }
  readUint8(t) {
    return this.bytes_[t];
  }
  readInt16(t) {
    return this.readUint16(t) << 16 >> 16;
  }
  readUint16(t) {
    return this.bytes_[t] | this.bytes_[t + 1] << 8;
  }
  readInt32(t) {
    return this.bytes_[t] | this.bytes_[t + 1] << 8 | this.bytes_[t + 2] << 16 | this.bytes_[t + 3] << 24;
  }
  readUint32(t) {
    return this.readInt32(t) >>> 0;
  }
  readInt64(t) {
    return BigInt.asIntN(64, BigInt(this.readUint32(t)) + (BigInt(this.readUint32(t + 4)) << BigInt(32)));
  }
  readUint64(t) {
    return BigInt.asUintN(64, BigInt(this.readUint32(t)) + (BigInt(this.readUint32(t + 4)) << BigInt(32)));
  }
  readFloat32(t) {
    return $t[0] = this.readInt32(t), os[0];
  }
  readFloat64(t) {
    return $t[an ? 0 : 1] = this.readInt32(t), $t[an ? 1 : 0] = this.readInt32(t + 4), as[0];
  }
  writeInt8(t, e) {
    this.bytes_[t] = e;
  }
  writeUint8(t, e) {
    this.bytes_[t] = e;
  }
  writeInt16(t, e) {
    this.bytes_[t] = e, this.bytes_[t + 1] = e >> 8;
  }
  writeUint16(t, e) {
    this.bytes_[t] = e, this.bytes_[t + 1] = e >> 8;
  }
  writeInt32(t, e) {
    this.bytes_[t] = e, this.bytes_[t + 1] = e >> 8, this.bytes_[t + 2] = e >> 16, this.bytes_[t + 3] = e >> 24;
  }
  writeUint32(t, e) {
    this.bytes_[t] = e, this.bytes_[t + 1] = e >> 8, this.bytes_[t + 2] = e >> 16, this.bytes_[t + 3] = e >> 24;
  }
  writeInt64(t, e) {
    this.writeInt32(t, Number(BigInt.asIntN(32, e))), this.writeInt32(t + 4, Number(BigInt.asIntN(32, e >> BigInt(32))));
  }
  writeUint64(t, e) {
    this.writeUint32(t, Number(BigInt.asUintN(32, e))), this.writeUint32(t + 4, Number(BigInt.asUintN(32, e >> BigInt(32))));
  }
  writeFloat32(t, e) {
    os[0] = e, this.writeInt32(t, $t[0]);
  }
  writeFloat64(t, e) {
    as[0] = e, this.writeInt32(t, $t[an ? 0 : 1]), this.writeInt32(t + 4, $t[an ? 1 : 0]);
  }
  /**
   * Return the file identifier.   Behavior is undefined for FlatBuffers whose
   * schema does not include a file_identifier (likely points at padding or the
   * start of a the root vtable).
   */
  getBufferIdentifier() {
    if (this.bytes_.length < this.position_ + St + Tt)
      throw new Error("FlatBuffers: ByteBuffer is too short to contain an identifier.");
    let t = "";
    for (let e = 0; e < Tt; e++)
      t += String.fromCharCode(this.readInt8(this.position_ + St + e));
    return t;
  }
  /**
   * Look up a field in the vtable, return an offset into the object, or 0 if the
   * field is not present.
   */
  __offset(t, e) {
    const i = t - this.readInt32(t);
    return e < this.readInt16(i) ? this.readInt16(i + e) : 0;
  }
  /**
   * Initialize any Table-derived type to point to the union at the given offset.
   */
  __union(t, e) {
    return t.bb_pos = e + this.readInt32(e), t.bb = this, t;
  }
  /**
   * Create a JavaScript string from UTF-8 data stored inside the FlatBuffer.
   * This allocates a new string and converts to wide chars upon each access.
   *
   * To avoid the conversion to string, pass Encoding.UTF8_BYTES as the
   * "optionalEncoding" argument. This is useful for avoiding conversion when
   * the data will just be packaged back up in another FlatBuffer later on.
   *
   * @param offset
   * @param opt_encoding Defaults to UTF16_STRING
   */
  __string(t, e) {
    t += this.readInt32(t);
    const i = this.readInt32(t);
    t += St;
    const s = this.bytes_.subarray(t, t + i);
    return e === li.UTF8_BYTES ? s : this.text_decoder_.decode(s);
  }
  /**
   * Handle unions that can contain string as its member, if a Table-derived type then initialize it,
   * if a string then return a new one
   *
   * WARNING: strings are immutable in JS so we can't change the string that the user gave us, this
   * makes the behaviour of __union_with_string different compared to __union
   */
  __union_with_string(t, e) {
    return typeof t == "string" ? this.__string(e) : this.__union(t, e);
  }
  /**
   * Retrieve the relative offset stored at "offset"
   */
  __indirect(t) {
    return t + this.readInt32(t);
  }
  /**
   * Get the start of data of a vector whose offset is stored at "offset" in this object.
   */
  __vector(t) {
    return t + this.readInt32(t) + St;
  }
  /**
   * Get the length of a vector whose offset is stored at "offset" in this object.
   */
  __vector_len(t) {
    return this.readInt32(t + this.readInt32(t));
  }
  __has_identifier(t) {
    if (t.length != Tt)
      throw new Error("FlatBuffers: file identifier must be length " + Tt);
    for (let e = 0; e < Tt; e++)
      if (t.charCodeAt(e) != this.readInt8(this.position() + St + e))
        return !1;
    return !0;
  }
  /**
   * A helper function for generating list for obj api
   */
  createScalarList(t, e) {
    const i = [];
    for (let s = 0; s < e; ++s) {
      const r = t(s);
      r !== null && i.push(r);
    }
    return i;
  }
  /**
   * A helper function for generating list for obj api
   * @param listAccessor function that accepts an index and return data at that index
   * @param listLength listLength
   * @param res result list
   */
  createObjList(t, e) {
    const i = [];
    for (let s = 0; s < e; ++s) {
      const r = t(s);
      r !== null && i.push(r.unpack());
    }
    return i;
  }
}, Ps = class js {
  /**
   * Create a FlatBufferBuilder.
   */
  constructor(t) {
    this.minalign = 1, this.vtable = null, this.vtable_in_use = 0, this.isNested = !1, this.object_start = 0, this.vtables = [], this.vector_num_elems = 0, this.force_defaults = !1, this.string_maps = null, this.text_encoder = new TextEncoder();
    let e;
    t ? e = t : e = 1024, this.bb = Qt.allocate(e), this.space = e;
  }
  clear() {
    this.bb.clear(), this.space = this.bb.capacity(), this.minalign = 1, this.vtable = null, this.vtable_in_use = 0, this.isNested = !1, this.object_start = 0, this.vtables = [], this.vector_num_elems = 0, this.force_defaults = !1, this.string_maps = null;
  }
  /**
   * In order to save space, fields that are set to their default value
   * don't get serialized into the buffer. Forcing defaults provides a
   * way to manually disable this optimization.
   *
   * @param forceDefaults true always serializes default values
   */
  forceDefaults(t) {
    this.force_defaults = t;
  }
  /**
   * Get the ByteBuffer representing the FlatBuffer. Only call this after you've
   * called finish(). The actual data starts at the ByteBuffer's current position,
   * not necessarily at 0.
   */
  dataBuffer() {
    return this.bb;
  }
  /**
   * Get the bytes representing the FlatBuffer. Only call this after you've
   * called finish().
   */
  asUint8Array() {
    return this.bb.bytes().subarray(this.bb.position(), this.bb.position() + this.offset());
  }
  /**
   * Prepare to write an element of `size` after `additional_bytes` have been
   * written, e.g. if you write a string, you need to align such the int length
   * field is aligned to 4 bytes, and the string data follows it directly. If all
   * you need to do is alignment, `additional_bytes` will be 0.
   *
   * @param size This is the of the new element to write
   * @param additional_bytes The padding size
   */
  prep(t, e) {
    t > this.minalign && (this.minalign = t);
    const i = ~(this.bb.capacity() - this.space + e) + 1 & t - 1;
    for (; this.space < i + t + e; ) {
      const s = this.bb.capacity();
      this.bb = js.growByteBuffer(this.bb), this.space += this.bb.capacity() - s;
    }
    this.pad(i);
  }
  pad(t) {
    for (let e = 0; e < t; e++)
      this.bb.writeInt8(--this.space, 0);
  }
  writeInt8(t) {
    this.bb.writeInt8(this.space -= 1, t);
  }
  writeInt16(t) {
    this.bb.writeInt16(this.space -= 2, t);
  }
  writeInt32(t) {
    this.bb.writeInt32(this.space -= 4, t);
  }
  writeInt64(t) {
    this.bb.writeInt64(this.space -= 8, t);
  }
  writeFloat32(t) {
    this.bb.writeFloat32(this.space -= 4, t);
  }
  writeFloat64(t) {
    this.bb.writeFloat64(this.space -= 8, t);
  }
  /**
   * Add an `int8` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int8` to add the buffer.
   */
  addInt8(t) {
    this.prep(1, 0), this.writeInt8(t);
  }
  /**
   * Add an `int16` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int16` to add the buffer.
   */
  addInt16(t) {
    this.prep(2, 0), this.writeInt16(t);
  }
  /**
   * Add an `int32` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int32` to add the buffer.
   */
  addInt32(t) {
    this.prep(4, 0), this.writeInt32(t);
  }
  /**
   * Add an `int64` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `int64` to add the buffer.
   */
  addInt64(t) {
    this.prep(8, 0), this.writeInt64(t);
  }
  /**
   * Add a `float32` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `float32` to add the buffer.
   */
  addFloat32(t) {
    this.prep(4, 0), this.writeFloat32(t);
  }
  /**
   * Add a `float64` to the buffer, properly aligned, and grows the buffer (if necessary).
   * @param value The `float64` to add the buffer.
   */
  addFloat64(t) {
    this.prep(8, 0), this.writeFloat64(t);
  }
  addFieldInt8(t, e, i) {
    (this.force_defaults || e != i) && (this.addInt8(e), this.slot(t));
  }
  addFieldInt16(t, e, i) {
    (this.force_defaults || e != i) && (this.addInt16(e), this.slot(t));
  }
  addFieldInt32(t, e, i) {
    (this.force_defaults || e != i) && (this.addInt32(e), this.slot(t));
  }
  addFieldInt64(t, e, i) {
    (this.force_defaults || e !== i) && (this.addInt64(e), this.slot(t));
  }
  addFieldFloat32(t, e, i) {
    (this.force_defaults || e != i) && (this.addFloat32(e), this.slot(t));
  }
  addFieldFloat64(t, e, i) {
    (this.force_defaults || e != i) && (this.addFloat64(e), this.slot(t));
  }
  addFieldOffset(t, e, i) {
    (this.force_defaults || e != i) && (this.addOffset(e), this.slot(t));
  }
  /**
   * Structs are stored inline, so nothing additional is being added. `d` is always 0.
   */
  addFieldStruct(t, e, i) {
    e != i && (this.nested(e), this.slot(t));
  }
  /**
   * Structures are always stored inline, they need to be created right
   * where they're used.  You'll get this assertion failure if you
   * created it elsewhere.
   */
  nested(t) {
    if (t != this.offset())
      throw new TypeError("FlatBuffers: struct must be serialized inline.");
  }
  /**
   * Should not be creating any other object, string or vector
   * while an object is being constructed
   */
  notNested() {
    if (this.isNested)
      throw new TypeError("FlatBuffers: object serialization must not be nested.");
  }
  /**
   * Set the current vtable at `voffset` to the current location in the buffer.
   */
  slot(t) {
    this.vtable !== null && (this.vtable[t] = this.offset());
  }
  /**
   * @returns Offset relative to the end of the buffer.
   */
  offset() {
    return this.bb.capacity() - this.space;
  }
  /**
   * Doubles the size of the backing ByteBuffer and copies the old data towards
   * the end of the new buffer (since we build the buffer backwards).
   *
   * @param bb The current buffer with the existing data
   * @returns A new byte buffer with the old data copied
   * to it. The data is located at the end of the buffer.
   *
   * uint8Array.set() formally takes {Array<number>|ArrayBufferView}, so to pass
   * it a uint8Array we need to suppress the type check:
   * @suppress {checkTypes}
   */
  static growByteBuffer(t) {
    const e = t.capacity();
    if (e & 3221225472)
      throw new Error("FlatBuffers: cannot grow buffer beyond 2 gigabytes.");
    const i = e << 1, s = Qt.allocate(i);
    return s.setPosition(i - e), s.bytes().set(t.bytes(), i - e), s;
  }
  /**
   * Adds on offset, relative to where it will be written.
   *
   * @param offset The offset to add.
   */
  addOffset(t) {
    this.prep(St, 0), this.writeInt32(this.offset() - t + St);
  }
  /**
   * Start encoding a new object in the buffer.  Users will not usually need to
   * call this directly. The FlatBuffers compiler will generate helper methods
   * that call this method internally.
   */
  startObject(t) {
    this.notNested(), this.vtable == null && (this.vtable = []), this.vtable_in_use = t;
    for (let e = 0; e < t; e++)
      this.vtable[e] = 0;
    this.isNested = !0, this.object_start = this.offset();
  }
  /**
   * Finish off writing the object that is under construction.
   *
   * @returns The offset to the object inside `dataBuffer`
   */
  endObject() {
    if (this.vtable == null || !this.isNested)
      throw new Error("FlatBuffers: endObject called without startObject");
    this.addInt32(0);
    const t = this.offset();
    let e = this.vtable_in_use - 1;
    for (; e >= 0 && this.vtable[e] == 0; e--)
      ;
    const i = e + 1;
    for (; e >= 0; e--)
      this.addInt16(this.vtable[e] != 0 ? t - this.vtable[e] : 0);
    const s = 2;
    this.addInt16(t - this.object_start);
    const r = (i + s) * Qn;
    this.addInt16(r);
    let o = 0;
    const a = this.space;
    t: for (e = 0; e < this.vtables.length; e++) {
      const c = this.bb.capacity() - this.vtables[e];
      if (r == this.bb.readInt16(c)) {
        for (let u = Qn; u < r; u += Qn)
          if (this.bb.readInt16(a + u) != this.bb.readInt16(c + u))
            continue t;
        o = this.vtables[e];
        break;
      }
    }
    return o ? (this.space = this.bb.capacity() - t, this.bb.writeInt32(this.space, o - t)) : (this.vtables.push(this.offset()), this.bb.writeInt32(this.bb.capacity() - t, this.offset() - t)), this.isNested = !1, t;
  }
  /**
   * Finalize a buffer, poiting to the given `root_table`.
   */
  finish(t, e, i) {
    const s = i ? E : 0;
    if (e) {
      const r = e;
      if (this.prep(this.minalign, St + Tt + s), r.length != Tt)
        throw new TypeError("FlatBuffers: file identifier must be length " + Tt);
      for (let o = Tt - 1; o >= 0; o--)
        this.writeInt8(r.charCodeAt(o));
    }
    this.prep(this.minalign, St + s), this.addOffset(t), s && this.addInt32(this.bb.capacity() - this.space), this.bb.setPosition(this.space);
  }
  /**
   * Finalize a size prefixed buffer, pointing to the given `root_table`.
   */
  finishSizePrefixed(t, e) {
    this.finish(t, e, !0);
  }
  /**
   * This checks a required field has been set in a given table that has
   * just been constructed.
   */
  requiredField(t, e) {
    const i = this.bb.capacity() - t, s = i - this.bb.readInt32(i);
    if (!(e < this.bb.readInt16(s) && this.bb.readInt16(s + e) != 0))
      throw new TypeError("FlatBuffers: field " + e + " must be set");
  }
  /**
   * Start a new array/vector of objects.  Users usually will not call
   * this directly. The FlatBuffers compiler will create a start/end
   * method for vector types in generated code.
   *
   * @param elem_size The size of each element in the array
   * @param num_elems The number of elements in the array
   * @param alignment The alignment of the array
   */
  startVector(t, e, i) {
    this.notNested(), this.vector_num_elems = e, this.prep(St, t * e), this.prep(i, t * e);
  }
  /**
   * Finish off the creation of an array and all its elements. The array must be
   * created with `startVector`.
   *
   * @returns The offset at which the newly created array
   * starts.
   */
  endVector() {
    return this.writeInt32(this.vector_num_elems), this.offset();
  }
  /**
   * Encode the string `s` in the buffer using UTF-8. If the string passed has
   * already been seen, we return the offset of the already written string
   *
   * @param s The string to encode
   * @return The offset in the buffer where the encoded string starts
   */
  createSharedString(t) {
    if (!t)
      return 0;
    if (this.string_maps || (this.string_maps = /* @__PURE__ */ new Map()), this.string_maps.has(t))
      return this.string_maps.get(t);
    const e = this.createString(t);
    return this.string_maps.set(t, e), e;
  }
  /**
   * Encode the string `s` in the buffer using UTF-8. If a Uint8Array is passed
   * instead of a string, it is assumed to contain valid UTF-8 encoded data.
   *
   * @param s The string to encode
   * @return The offset in the buffer where the encoded string starts
   */
  createString(t) {
    if (t == null)
      return 0;
    let e;
    return t instanceof Uint8Array ? e = t : e = this.text_encoder.encode(t), this.addInt8(0), this.startVector(1, e.length, 1), this.bb.setPosition(this.space -= e.length), this.bb.bytes().set(e, this.space), this.endVector();
  }
  /**
   * Create a byte vector.
   *
   * @param v The bytes to add
   * @returns The offset in the buffer where the byte vector starts
   */
  createByteVector(t) {
    return t == null ? 0 : (this.startVector(1, t.length, 1), this.bb.setPosition(this.space -= t.length), this.bb.bytes().set(t, this.space), this.endVector());
  }
  /**
   * A helper function to pack an object
   *
   * @returns offset of obj
   */
  createObjectOffset(t) {
    return t === null ? 0 : typeof t == "string" ? this.createString(t) : t.pack(this);
  }
  /**
   * A helper function to pack a list of object
   *
   * @returns list of offsets of each non null object
   */
  createObjectOffsetList(t) {
    const e = [];
    for (let i = 0; i < t.length; ++i) {
      const s = t[i];
      if (s !== null)
        e.push(this.createObjectOffset(s));
      else
        throw new TypeError("FlatBuffers: Argument for createObjectOffsetList cannot contain null.");
    }
    return e;
  }
  createStructOffsetList(t, e) {
    return e(this, t.length), this.createObjectOffsetList(t.slice().reverse()), this.endVector();
  }
};
var je;
(function(n) {
  n[n.BUFFER = 0] = "BUFFER";
})(je || (je = {}));
var Xt;
(function(n) {
  n[n.LZ4_FRAME = 0] = "LZ4_FRAME", n[n.ZSTD = 1] = "ZSTD";
})(Xt || (Xt = {}));
let Le = class Gt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsBodyCompression(t, e) {
    return (e || new Gt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsBodyCompression(t, e) {
    return t.setPosition(t.position() + E), (e || new Gt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * Compressor library.
   * For LZ4_FRAME, each compressed buffer must consist of a single frame.
   */
  codec() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt8(this.bb_pos + t) : Xt.LZ4_FRAME;
  }
  /**
   * Indicates the way the record batch body was compressed
   */
  method() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.readInt8(this.bb_pos + t) : je.BUFFER;
  }
  static startBodyCompression(t) {
    t.startObject(2);
  }
  static addCodec(t, e) {
    t.addFieldInt8(0, e, Xt.LZ4_FRAME);
  }
  static addMethod(t, e) {
    t.addFieldInt8(1, e, je.BUFFER);
  }
  static endBodyCompression(t) {
    return t.endObject();
  }
  static createBodyCompression(t, e, i) {
    return Gt.startBodyCompression(t), Gt.addCodec(t, e), Gt.addMethod(t, i), Gt.endBodyCompression(t);
  }
};
class $s {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  /**
   * The relative offset into the shared memory page where the bytes for this
   * buffer starts
   */
  offset() {
    return this.bb.readInt64(this.bb_pos);
  }
  /**
   * The absolute length (in bytes) of the memory buffer. The memory is found
   * from offset (inclusive) to offset + length (non-inclusive). When building
   * messages using the encapsulated IPC message, padding bytes may be written
   * after a buffer, but such padding bytes do not need to be accounted for in
   * the size here.
   */
  length() {
    return this.bb.readInt64(this.bb_pos + 8);
  }
  static sizeOf() {
    return 16;
  }
  static createBuffer(t, e, i) {
    return t.prep(8, 16), t.writeInt64(BigInt(i ?? 0)), t.writeInt64(BigInt(e ?? 0)), t.offset();
  }
}
let Ys = class {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  /**
   * The number of value slots in the Arrow array at this level of a nested
   * tree
   */
  length() {
    return this.bb.readInt64(this.bb_pos);
  }
  /**
   * The number of observed nulls. Fields with null_count == 0 may choose not
   * to write their physical validity bitmap out as a materialized buffer,
   * instead setting the length of the bitmap buffer to 0.
   */
  nullCount() {
    return this.bb.readInt64(this.bb_pos + 8);
  }
  static sizeOf() {
    return 16;
  }
  static createFieldNode(t, e, i) {
    return t.prep(8, 16), t.writeInt64(BigInt(i ?? 0)), t.writeInt64(BigInt(e ?? 0)), t.offset();
  }
}, _t = class ui {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsRecordBatch(t, e) {
    return (e || new ui()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsRecordBatch(t, e) {
    return t.setPosition(t.position() + E), (e || new ui()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * number of records / rows. The arrays in the batch should all have this
   * length
   */
  length() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt64(this.bb_pos + t) : BigInt("0");
  }
  /**
   * Nodes correspond to the pre-ordered flattened logical schema
   */
  nodes(t, e) {
    const i = this.bb.__offset(this.bb_pos, 6);
    return i ? (e || new Ys()).__init(this.bb.__vector(this.bb_pos + i) + t * 16, this.bb) : null;
  }
  nodesLength() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  /**
   * Buffers correspond to the pre-ordered flattened buffer tree
   *
   * The number of buffers appended to this list depends on the schema. For
   * example, most primitive arrays will have 2 buffers, 1 for the validity
   * bitmap and 1 for the values. For struct arrays, there will only be a
   * single buffer for the validity (nulls) bitmap
   */
  buffers(t, e) {
    const i = this.bb.__offset(this.bb_pos, 8);
    return i ? (e || new $s()).__init(this.bb.__vector(this.bb_pos + i) + t * 16, this.bb) : null;
  }
  buffersLength() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  /**
   * Optional compression of the message body
   */
  compression(t) {
    const e = this.bb.__offset(this.bb_pos, 10);
    return e ? (t || new Le()).__init(this.bb.__indirect(this.bb_pos + e), this.bb) : null;
  }
  static startRecordBatch(t) {
    t.startObject(4);
  }
  static addLength(t, e) {
    t.addFieldInt64(0, e, BigInt("0"));
  }
  static addNodes(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static startNodesVector(t, e) {
    t.startVector(16, e, 8);
  }
  static addBuffers(t, e) {
    t.addFieldOffset(2, e, 0);
  }
  static startBuffersVector(t, e) {
    t.startVector(16, e, 8);
  }
  static addCompression(t, e) {
    t.addFieldOffset(3, e, 0);
  }
  static endRecordBatch(t) {
    return t.endObject();
  }
}, ie = class di {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsDictionaryBatch(t, e) {
    return (e || new di()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsDictionaryBatch(t, e) {
    return t.setPosition(t.position() + E), (e || new di()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  id() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt64(this.bb_pos + t) : BigInt("0");
  }
  data(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? (t || new _t()).__init(this.bb.__indirect(this.bb_pos + e), this.bb) : null;
  }
  /**
   * If isDelta is true the values in the dictionary are to be appended to a
   * dictionary with the indicated id. If isDelta is false this dictionary
   * should replace the existing dictionary.
   */
  isDelta() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? !!this.bb.readInt8(this.bb_pos + t) : !1;
  }
  static startDictionaryBatch(t) {
    t.startObject(3);
  }
  static addId(t, e) {
    t.addFieldInt64(0, e, BigInt("0"));
  }
  static addData(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static addIsDelta(t, e) {
    t.addFieldInt8(2, +e, 0);
  }
  static endDictionaryBatch(t) {
    return t.endObject();
  }
};
var _e;
(function(n) {
  n[n.Little = 0] = "Little", n[n.Big = 1] = "Big";
})(_e || (_e = {}));
var _n;
(function(n) {
  n[n.DenseArray = 0] = "DenseArray";
})(_n || (_n = {}));
class st {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsInt(t, e) {
    return (e || new st()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsInt(t, e) {
    return t.setPosition(t.position() + E), (e || new st()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  bitWidth() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt32(this.bb_pos + t) : 0;
  }
  isSigned() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? !!this.bb.readInt8(this.bb_pos + t) : !1;
  }
  static startInt(t) {
    t.startObject(2);
  }
  static addBitWidth(t, e) {
    t.addFieldInt32(0, e, 0);
  }
  static addIsSigned(t, e) {
    t.addFieldInt8(1, +e, 0);
  }
  static endInt(t) {
    return t.endObject();
  }
  static createInt(t, e, i) {
    return st.startInt(t), st.addBitWidth(t, e), st.addIsSigned(t, i), st.endInt(t);
  }
}
class Lt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsDictionaryEncoding(t, e) {
    return (e || new Lt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsDictionaryEncoding(t, e) {
    return t.setPosition(t.position() + E), (e || new Lt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * The known dictionary id in the application where this data is used. In
   * the file or streaming formats, the dictionary ids are found in the
   * DictionaryBatch messages
   */
  id() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt64(this.bb_pos + t) : BigInt("0");
  }
  /**
   * The dictionary indices are constrained to be non-negative integers. If
   * this field is null, the indices must be signed int32. To maximize
   * cross-language compatibility and performance, implementations are
   * recommended to prefer signed integer types over unsigned integer types
   * and to avoid uint64 indices unless they are required by an application.
   */
  indexType(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? (t || new st()).__init(this.bb.__indirect(this.bb_pos + e), this.bb) : null;
  }
  /**
   * By default, dictionaries are not ordered, or the order does not have
   * semantic meaning. In some statistical, applications, dictionary-encoding
   * is used to represent ordered categorical data, and we provide a way to
   * preserve that metadata here
   */
  isOrdered() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? !!this.bb.readInt8(this.bb_pos + t) : !1;
  }
  dictionaryKind() {
    const t = this.bb.__offset(this.bb_pos, 10);
    return t ? this.bb.readInt16(this.bb_pos + t) : _n.DenseArray;
  }
  static startDictionaryEncoding(t) {
    t.startObject(4);
  }
  static addId(t, e) {
    t.addFieldInt64(0, e, BigInt("0"));
  }
  static addIndexType(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static addIsOrdered(t, e) {
    t.addFieldInt8(2, +e, 0);
  }
  static addDictionaryKind(t, e) {
    t.addFieldInt16(3, e, _n.DenseArray);
  }
  static endDictionaryEncoding(t) {
    return t.endObject();
  }
}
class Y {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsKeyValue(t, e) {
    return (e || new Y()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsKeyValue(t, e) {
    return t.setPosition(t.position() + E), (e || new Y()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  key(t) {
    const e = this.bb.__offset(this.bb_pos, 4);
    return e ? this.bb.__string(this.bb_pos + e, t) : null;
  }
  value(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? this.bb.__string(this.bb_pos + e, t) : null;
  }
  static startKeyValue(t) {
    t.startObject(2);
  }
  static addKey(t, e) {
    t.addFieldOffset(0, e, 0);
  }
  static addValue(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static endKeyValue(t) {
    return t.endObject();
  }
  static createKeyValue(t, e, i) {
    return Y.startKeyValue(t), Y.addKey(t, e), Y.addValue(t, i), Y.endKeyValue(t);
  }
}
let cs = class Ue {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsBinary(t, e) {
    return (e || new Ue()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsBinary(t, e) {
    return t.setPosition(t.position() + E), (e || new Ue()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startBinary(t) {
    t.startObject(0);
  }
  static endBinary(t) {
    return t.endObject();
  }
  static createBinary(t) {
    return Ue.startBinary(t), Ue.endBinary(t);
  }
}, ls = class xe {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsBool(t, e) {
    return (e || new xe()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsBool(t, e) {
    return t.setPosition(t.position() + E), (e || new xe()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startBool(t) {
    t.startObject(0);
  }
  static endBool(t) {
    return t.endObject();
  }
  static createBool(t) {
    return xe.startBool(t), xe.endBool(t);
  }
}, un = class se {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsDate(t, e) {
    return (e || new se()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsDate(t, e) {
    return t.setPosition(t.position() + E), (e || new se()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  unit() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : dt.MILLISECOND;
  }
  static startDate(t) {
    t.startObject(1);
  }
  static addUnit(t, e) {
    t.addFieldInt16(0, e, dt.MILLISECOND);
  }
  static endDate(t) {
    return t.endObject();
  }
  static createDate(t, e) {
    return se.startDate(t), se.addUnit(t, e), se.endDate(t);
  }
}, re = class jt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsDecimal(t, e) {
    return (e || new jt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsDecimal(t, e) {
    return t.setPosition(t.position() + E), (e || new jt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * Total number of decimal digits
   */
  precision() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt32(this.bb_pos + t) : 0;
  }
  /**
   * Number of digits after the decimal point "."
   */
  scale() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.readInt32(this.bb_pos + t) : 0;
  }
  /**
   * Number of bits per value. The only accepted widths are 128 and 256.
   * We use bitWidth for consistency with Int::bitWidth.
   */
  bitWidth() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? this.bb.readInt32(this.bb_pos + t) : 128;
  }
  static startDecimal(t) {
    t.startObject(3);
  }
  static addPrecision(t, e) {
    t.addFieldInt32(0, e, 0);
  }
  static addScale(t, e) {
    t.addFieldInt32(1, e, 0);
  }
  static addBitWidth(t, e) {
    t.addFieldInt32(2, e, 128);
  }
  static endDecimal(t) {
    return t.endObject();
  }
  static createDecimal(t, e, i, s) {
    return jt.startDecimal(t), jt.addPrecision(t, e), jt.addScale(t, i), jt.addBitWidth(t, s), jt.endDecimal(t);
  }
}, dn = class oe {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsDuration(t, e) {
    return (e || new oe()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsDuration(t, e) {
    return t.setPosition(t.position() + E), (e || new oe()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  unit() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : b.MILLISECOND;
  }
  static startDuration(t) {
    t.startObject(1);
  }
  static addUnit(t, e) {
    t.addFieldInt16(0, e, b.MILLISECOND);
  }
  static endDuration(t) {
    return t.endObject();
  }
  static createDuration(t, e) {
    return oe.startDuration(t), oe.addUnit(t, e), oe.endDuration(t);
  }
}, hn = class ae {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsFixedSizeBinary(t, e) {
    return (e || new ae()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsFixedSizeBinary(t, e) {
    return t.setPosition(t.position() + E), (e || new ae()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * Number of bytes per value
   */
  byteWidth() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt32(this.bb_pos + t) : 0;
  }
  static startFixedSizeBinary(t) {
    t.startObject(1);
  }
  static addByteWidth(t, e) {
    t.addFieldInt32(0, e, 0);
  }
  static endFixedSizeBinary(t) {
    return t.endObject();
  }
  static createFixedSizeBinary(t, e) {
    return ae.startFixedSizeBinary(t), ae.addByteWidth(t, e), ae.endFixedSizeBinary(t);
  }
}, fn = class ce {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsFixedSizeList(t, e) {
    return (e || new ce()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsFixedSizeList(t, e) {
    return t.setPosition(t.position() + E), (e || new ce()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * Number of list items per value
   */
  listSize() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt32(this.bb_pos + t) : 0;
  }
  static startFixedSizeList(t) {
    t.startObject(1);
  }
  static addListSize(t, e) {
    t.addFieldInt32(0, e, 0);
  }
  static endFixedSizeList(t) {
    return t.endObject();
  }
  static createFixedSizeList(t, e) {
    return ce.startFixedSizeList(t), ce.addListSize(t, e), ce.endFixedSizeList(t);
  }
};
class Bt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsFloatingPoint(t, e) {
    return (e || new Bt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsFloatingPoint(t, e) {
    return t.setPosition(t.position() + E), (e || new Bt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  precision() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : W.HALF;
  }
  static startFloatingPoint(t) {
    t.startObject(1);
  }
  static addPrecision(t, e) {
    t.addFieldInt16(0, e, W.HALF);
  }
  static endFloatingPoint(t) {
    return t.endObject();
  }
  static createFloatingPoint(t, e) {
    return Bt.startFloatingPoint(t), Bt.addPrecision(t, e), Bt.endFloatingPoint(t);
  }
}
class At {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsInterval(t, e) {
    return (e || new At()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsInterval(t, e) {
    return t.setPosition(t.position() + E), (e || new At()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  unit() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : J.YEAR_MONTH;
  }
  static startInterval(t) {
    t.startObject(1);
  }
  static addUnit(t, e) {
    t.addFieldInt16(0, e, J.YEAR_MONTH);
  }
  static endInterval(t) {
    return t.endObject();
  }
  static createInterval(t, e) {
    return At.startInterval(t), At.addUnit(t, e), At.endInterval(t);
  }
}
let us = class Ce {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsLargeBinary(t, e) {
    return (e || new Ce()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsLargeBinary(t, e) {
    return t.setPosition(t.position() + E), (e || new Ce()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startLargeBinary(t) {
    t.startObject(0);
  }
  static endLargeBinary(t) {
    return t.endObject();
  }
  static createLargeBinary(t) {
    return Ce.startLargeBinary(t), Ce.endLargeBinary(t);
  }
}, ds = class Ee {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsLargeUtf8(t, e) {
    return (e || new Ee()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsLargeUtf8(t, e) {
    return t.setPosition(t.position() + E), (e || new Ee()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startLargeUtf8(t) {
    t.startObject(0);
  }
  static endLargeUtf8(t) {
    return t.endObject();
  }
  static createLargeUtf8(t) {
    return Ee.startLargeUtf8(t), Ee.endLargeUtf8(t);
  }
}, hs = class Ve {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsList(t, e) {
    return (e || new Ve()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsList(t, e) {
    return t.setPosition(t.position() + E), (e || new Ve()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startList(t) {
    t.startObject(0);
  }
  static endList(t) {
    return t.endObject();
  }
  static createList(t) {
    return Ve.startList(t), Ve.endList(t);
  }
}, pn = class le {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsMap(t, e) {
    return (e || new le()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsMap(t, e) {
    return t.setPosition(t.position() + E), (e || new le()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * Set to true if the keys within each value are sorted
   */
  keysSorted() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? !!this.bb.readInt8(this.bb_pos + t) : !1;
  }
  static startMap(t) {
    t.startObject(1);
  }
  static addKeysSorted(t, e) {
    t.addFieldInt8(0, +e, 0);
  }
  static endMap(t) {
    return t.endObject();
  }
  static createMap(t, e) {
    return le.startMap(t), le.addKeysSorted(t, e), le.endMap(t);
  }
}, fs = class Re {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsNull(t, e) {
    return (e || new Re()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsNull(t, e) {
    return t.setPosition(t.position() + E), (e || new Re()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startNull(t) {
    t.startObject(0);
  }
  static endNull(t) {
    return t.endObject();
  }
  static createNull(t) {
    return Re.startNull(t), Re.endNull(t);
  }
};
class Zt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsStruct_(t, e) {
    return (e || new Zt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsStruct_(t, e) {
    return t.setPosition(t.position() + E), (e || new Zt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startStruct_(t) {
    t.startObject(0);
  }
  static endStruct_(t) {
    return t.endObject();
  }
  static createStruct_(t) {
    return Zt.startStruct_(t), Zt.endStruct_(t);
  }
}
class lt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsTime(t, e) {
    return (e || new lt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsTime(t, e) {
    return t.setPosition(t.position() + E), (e || new lt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  unit() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : b.MILLISECOND;
  }
  bitWidth() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.readInt32(this.bb_pos + t) : 32;
  }
  static startTime(t) {
    t.startObject(2);
  }
  static addUnit(t, e) {
    t.addFieldInt16(0, e, b.MILLISECOND);
  }
  static addBitWidth(t, e) {
    t.addFieldInt32(1, e, 32);
  }
  static endTime(t) {
    return t.endObject();
  }
  static createTime(t, e, i) {
    return lt.startTime(t), lt.addUnit(t, e), lt.addBitWidth(t, i), lt.endTime(t);
  }
}
class ut {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsTimestamp(t, e) {
    return (e || new ut()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsTimestamp(t, e) {
    return t.setPosition(t.position() + E), (e || new ut()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  unit() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : b.SECOND;
  }
  timezone(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? this.bb.__string(this.bb_pos + e, t) : null;
  }
  static startTimestamp(t) {
    t.startObject(2);
  }
  static addUnit(t, e) {
    t.addFieldInt16(0, e, b.SECOND);
  }
  static addTimezone(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static endTimestamp(t) {
    return t.endObject();
  }
  static createTimestamp(t, e, i) {
    return ut.startTimestamp(t), ut.addUnit(t, e), ut.addTimezone(t, i), ut.endTimestamp(t);
  }
}
class Q {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsUnion(t, e) {
    return (e || new Q()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsUnion(t, e) {
    return t.setPosition(t.position() + E), (e || new Q()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  mode() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : X.Sparse;
  }
  typeIds(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? this.bb.readInt32(this.bb.__vector(this.bb_pos + e) + t * 4) : 0;
  }
  typeIdsLength() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  typeIdsArray() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? new Int32Array(this.bb.bytes().buffer, this.bb.bytes().byteOffset + this.bb.__vector(this.bb_pos + t), this.bb.__vector_len(this.bb_pos + t)) : null;
  }
  static startUnion(t) {
    t.startObject(2);
  }
  static addMode(t, e) {
    t.addFieldInt16(0, e, X.Sparse);
  }
  static addTypeIds(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static createTypeIdsVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addInt32(e[i]);
    return t.endVector();
  }
  static startTypeIdsVector(t, e) {
    t.startVector(4, e, 4);
  }
  static endUnion(t) {
    return t.endObject();
  }
  static createUnion(t, e, i) {
    return Q.startUnion(t), Q.addMode(t, e), Q.addTypeIds(t, i), Q.endUnion(t);
  }
}
let ps = class ze {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsUtf8(t, e) {
    return (e || new ze()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsUtf8(t, e) {
    return t.setPosition(t.position() + E), (e || new ze()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static startUtf8(t) {
    t.startObject(0);
  }
  static endUtf8(t) {
    return t.endObject();
  }
  static createUtf8(t) {
    return ze.startUtf8(t), ze.endUtf8(t);
  }
};
var z;
(function(n) {
  n[n.NONE = 0] = "NONE", n[n.Null = 1] = "Null", n[n.Int = 2] = "Int", n[n.FloatingPoint = 3] = "FloatingPoint", n[n.Binary = 4] = "Binary", n[n.Utf8 = 5] = "Utf8", n[n.Bool = 6] = "Bool", n[n.Decimal = 7] = "Decimal", n[n.Date = 8] = "Date", n[n.Time = 9] = "Time", n[n.Timestamp = 10] = "Timestamp", n[n.Interval = 11] = "Interval", n[n.List = 12] = "List", n[n.Struct_ = 13] = "Struct_", n[n.Union = 14] = "Union", n[n.FixedSizeBinary = 15] = "FixedSizeBinary", n[n.FixedSizeList = 16] = "FixedSizeList", n[n.Map = 17] = "Map", n[n.Duration = 18] = "Duration", n[n.LargeBinary = 19] = "LargeBinary", n[n.LargeUtf8 = 20] = "LargeUtf8", n[n.LargeList = 21] = "LargeList", n[n.RunEndEncoded = 22] = "RunEndEncoded";
})(z || (z = {}));
let at = class yn {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsField(t, e) {
    return (e || new yn()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsField(t, e) {
    return t.setPosition(t.position() + E), (e || new yn()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  name(t) {
    const e = this.bb.__offset(this.bb_pos, 4);
    return e ? this.bb.__string(this.bb_pos + e, t) : null;
  }
  /**
   * Whether or not this field can contain nulls. Should be true in general.
   */
  nullable() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? !!this.bb.readInt8(this.bb_pos + t) : !1;
  }
  typeType() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? this.bb.readUint8(this.bb_pos + t) : z.NONE;
  }
  /**
   * This is the type of the decoded value if the field is dictionary encoded.
   */
  type(t) {
    const e = this.bb.__offset(this.bb_pos, 10);
    return e ? this.bb.__union(t, this.bb_pos + e) : null;
  }
  /**
   * Present only if the field is dictionary encoded.
   */
  dictionary(t) {
    const e = this.bb.__offset(this.bb_pos, 12);
    return e ? (t || new Lt()).__init(this.bb.__indirect(this.bb_pos + e), this.bb) : null;
  }
  /**
   * children apply only to nested data types like Struct, List and Union. For
   * primitive types children will have length 0.
   */
  children(t, e) {
    const i = this.bb.__offset(this.bb_pos, 14);
    return i ? (e || new yn()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  childrenLength() {
    const t = this.bb.__offset(this.bb_pos, 14);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  /**
   * User-defined metadata
   */
  customMetadata(t, e) {
    const i = this.bb.__offset(this.bb_pos, 16);
    return i ? (e || new Y()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  customMetadataLength() {
    const t = this.bb.__offset(this.bb_pos, 16);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  static startField(t) {
    t.startObject(7);
  }
  static addName(t, e) {
    t.addFieldOffset(0, e, 0);
  }
  static addNullable(t, e) {
    t.addFieldInt8(1, +e, 0);
  }
  static addTypeType(t, e) {
    t.addFieldInt8(2, e, z.NONE);
  }
  static addType(t, e) {
    t.addFieldOffset(3, e, 0);
  }
  static addDictionary(t, e) {
    t.addFieldOffset(4, e, 0);
  }
  static addChildren(t, e) {
    t.addFieldOffset(5, e, 0);
  }
  static createChildrenVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startChildrenVector(t, e) {
    t.startVector(4, e, 4);
  }
  static addCustomMetadata(t, e) {
    t.addFieldOffset(6, e, 0);
  }
  static createCustomMetadataVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startCustomMetadataVector(t, e) {
    t.startVector(4, e, 4);
  }
  static endField(t) {
    return t.endObject();
  }
}, vt = class Mt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsSchema(t, e) {
    return (e || new Mt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsSchema(t, e) {
    return t.setPosition(t.position() + E), (e || new Mt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  /**
   * endianness of the buffer
   * it is Little Endian by default
   * if endianness doesn't match the underlying system then the vectors need to be converted
   */
  endianness() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : _e.Little;
  }
  fields(t, e) {
    const i = this.bb.__offset(this.bb_pos, 6);
    return i ? (e || new at()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  fieldsLength() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  customMetadata(t, e) {
    const i = this.bb.__offset(this.bb_pos, 8);
    return i ? (e || new Y()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  customMetadataLength() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  /**
   * Features used in the stream/file.
   */
  features(t) {
    const e = this.bb.__offset(this.bb_pos, 10);
    return e ? this.bb.readInt64(this.bb.__vector(this.bb_pos + e) + t * 8) : BigInt(0);
  }
  featuresLength() {
    const t = this.bb.__offset(this.bb_pos, 10);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  static startSchema(t) {
    t.startObject(4);
  }
  static addEndianness(t, e) {
    t.addFieldInt16(0, e, _e.Little);
  }
  static addFields(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static createFieldsVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startFieldsVector(t, e) {
    t.startVector(4, e, 4);
  }
  static addCustomMetadata(t, e) {
    t.addFieldOffset(2, e, 0);
  }
  static createCustomMetadataVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startCustomMetadataVector(t, e) {
    t.startVector(4, e, 4);
  }
  static addFeatures(t, e) {
    t.addFieldOffset(3, e, 0);
  }
  static createFeaturesVector(t, e) {
    t.startVector(8, e.length, 8);
    for (let i = e.length - 1; i >= 0; i--)
      t.addInt64(e[i]);
    return t.endVector();
  }
  static startFeaturesVector(t, e) {
    t.startVector(8, e, 8);
  }
  static endSchema(t) {
    return t.endObject();
  }
  static finishSchemaBuffer(t, e) {
    t.finish(e);
  }
  static finishSizePrefixedSchemaBuffer(t, e) {
    t.finish(e, void 0, !0);
  }
  static createSchema(t, e, i, s, r) {
    return Mt.startSchema(t), Mt.addEndianness(t, e), Mt.addFields(t, i), Mt.addCustomMetadata(t, s), Mt.addFeatures(t, r), Mt.endSchema(t);
  }
};
var x;
(function(n) {
  n[n.NONE = 0] = "NONE", n[n.Schema = 1] = "Schema", n[n.DictionaryBatch = 2] = "DictionaryBatch", n[n.RecordBatch = 3] = "RecordBatch", n[n.Tensor = 4] = "Tensor", n[n.SparseTensor = 5] = "SparseTensor";
})(x || (x = {}));
var l;
(function(n) {
  n[n.NONE = 0] = "NONE", n[n.Null = 1] = "Null", n[n.Int = 2] = "Int", n[n.Float = 3] = "Float", n[n.Binary = 4] = "Binary", n[n.Utf8 = 5] = "Utf8", n[n.Bool = 6] = "Bool", n[n.Decimal = 7] = "Decimal", n[n.Date = 8] = "Date", n[n.Time = 9] = "Time", n[n.Timestamp = 10] = "Timestamp", n[n.Interval = 11] = "Interval", n[n.List = 12] = "List", n[n.Struct = 13] = "Struct", n[n.Union = 14] = "Union", n[n.FixedSizeBinary = 15] = "FixedSizeBinary", n[n.FixedSizeList = 16] = "FixedSizeList", n[n.Map = 17] = "Map", n[n.Duration = 18] = "Duration", n[n.LargeBinary = 19] = "LargeBinary", n[n.LargeUtf8 = 20] = "LargeUtf8", n[n.Dictionary = -1] = "Dictionary", n[n.Int8 = -2] = "Int8", n[n.Int16 = -3] = "Int16", n[n.Int32 = -4] = "Int32", n[n.Int64 = -5] = "Int64", n[n.Uint8 = -6] = "Uint8", n[n.Uint16 = -7] = "Uint16", n[n.Uint32 = -8] = "Uint32", n[n.Uint64 = -9] = "Uint64", n[n.Float16 = -10] = "Float16", n[n.Float32 = -11] = "Float32", n[n.Float64 = -12] = "Float64", n[n.DateDay = -13] = "DateDay", n[n.DateMillisecond = -14] = "DateMillisecond", n[n.TimestampSecond = -15] = "TimestampSecond", n[n.TimestampMillisecond = -16] = "TimestampMillisecond", n[n.TimestampMicrosecond = -17] = "TimestampMicrosecond", n[n.TimestampNanosecond = -18] = "TimestampNanosecond", n[n.TimeSecond = -19] = "TimeSecond", n[n.TimeMillisecond = -20] = "TimeMillisecond", n[n.TimeMicrosecond = -21] = "TimeMicrosecond", n[n.TimeNanosecond = -22] = "TimeNanosecond", n[n.DenseUnion = -23] = "DenseUnion", n[n.SparseUnion = -24] = "SparseUnion", n[n.IntervalDayTime = -25] = "IntervalDayTime", n[n.IntervalYearMonth = -26] = "IntervalYearMonth", n[n.DurationSecond = -27] = "DurationSecond", n[n.DurationMillisecond = -28] = "DurationMillisecond", n[n.DurationMicrosecond = -29] = "DurationMicrosecond", n[n.DurationNanosecond = -30] = "DurationNanosecond", n[n.IntervalMonthDayNano = -31] = "IntervalMonthDayNano";
})(l || (l = {}));
var Nt;
(function(n) {
  n[n.OFFSET = 0] = "OFFSET", n[n.DATA = 1] = "DATA", n[n.VALIDITY = 2] = "VALIDITY", n[n.TYPE = 3] = "TYPE";
})(Nt || (Nt = {}));
const Da = void 0;
function te(n) {
  if (n === null)
    return "null";
  if (n === Da)
    return "undefined";
  switch (typeof n) {
    case "number":
      return `${n}`;
    case "bigint":
      return `${n}`;
    case "string":
      return `"${n}"`;
  }
  return typeof n[Symbol.toPrimitive] == "function" ? n[Symbol.toPrimitive]("string") : ArrayBuffer.isView(n) ? n instanceof BigInt64Array || n instanceof BigUint64Array ? `[${[...n].map((t) => te(t))}]` : `[${n}]` : ArrayBuffer.isView(n) ? `[${n}]` : JSON.stringify(n, (t, e) => typeof e == "bigint" ? `${e}` : e);
}
function k(n) {
  if (typeof n == "bigint" && (n < Number.MIN_SAFE_INTEGER || n > Number.MAX_SAFE_INTEGER))
    throw new TypeError(`${n} is not safe to convert to a number.`);
  return Number(n);
}
function Ws(n, t) {
  return k(n / t) + k(n % t) / k(t);
}
const Oa = /* @__PURE__ */ Symbol.for("isArrowBigNum");
function mt(n, ...t) {
  return t.length === 0 ? Object.setPrototypeOf(R(this.TypedArray, n), this.constructor.prototype) : Object.setPrototypeOf(new this.TypedArray(n, ...t), this.constructor.prototype);
}
mt.prototype[Oa] = !0;
mt.prototype.toJSON = function() {
  return `"${Ye(this)}"`;
};
mt.prototype.valueOf = function(n) {
  return Hs(this, n);
};
mt.prototype.toString = function() {
  return Ye(this);
};
mt.prototype[Symbol.toPrimitive] = function(n = "default") {
  switch (n) {
    case "number":
      return Hs(this);
    case "string":
      return Ye(this);
    case "default":
      return Na(this);
  }
  return Ye(this);
};
function ye(...n) {
  return mt.apply(this, n);
}
function ge(...n) {
  return mt.apply(this, n);
}
function $e(...n) {
  return mt.apply(this, n);
}
Object.setPrototypeOf(ye.prototype, Object.create(Int32Array.prototype));
Object.setPrototypeOf(ge.prototype, Object.create(Uint32Array.prototype));
Object.setPrototypeOf($e.prototype, Object.create(Uint32Array.prototype));
Object.assign(ye.prototype, mt.prototype, { constructor: ye, signed: !0, TypedArray: Int32Array, BigIntArray: BigInt64Array });
Object.assign(ge.prototype, mt.prototype, { constructor: ge, signed: !1, TypedArray: Uint32Array, BigIntArray: BigUint64Array });
Object.assign($e.prototype, mt.prototype, { constructor: $e, signed: !0, TypedArray: Uint32Array, BigIntArray: BigUint64Array });
const Fa = BigInt(4294967296) * BigInt(4294967296), Ma = Fa - BigInt(1);
function Hs(n, t) {
  const { buffer: e, byteOffset: i, byteLength: s, signed: r } = n, o = new BigUint64Array(e, i, s / 8), a = r && o.at(-1) & BigInt(1) << BigInt(63);
  let c = BigInt(0), u = 0;
  if (a) {
    for (const d of o)
      c |= (d ^ Ma) * (BigInt(1) << BigInt(64 * u++));
    c *= BigInt(-1), c -= BigInt(1);
  } else
    for (const d of o)
      c |= d * (BigInt(1) << BigInt(64 * u++));
  if (typeof t == "number" && t > 0) {
    const d = BigInt("1".padEnd(t + 1, "0")), h = c / d, T = a ? -(c % d) : c % d, O = k(h), j = `${T}`.padStart(t, "0");
    return +`${a && O === 0 ? "-" : ""}${O}.${j}`;
  }
  return k(c);
}
function Ye(n) {
  if (n.byteLength === 8)
    return `${new n.BigIntArray(n.buffer, n.byteOffset, 1)[0]}`;
  if (!n.signed)
    return Xn(n);
  let t = new Uint16Array(n.buffer, n.byteOffset, n.byteLength / 2);
  if (new Int16Array([t.at(-1)])[0] >= 0)
    return Xn(n);
  t = t.slice();
  let i = 1;
  for (let r = 0; r < t.length; r++) {
    const o = t[r], a = ~o + i;
    t[r] = a, i &= o === 0 ? 1 : 0;
  }
  return `-${Xn(t)}`;
}
function Na(n) {
  return n.byteLength === 8 ? new n.BigIntArray(n.buffer, n.byteOffset, 1)[0] : Ye(n);
}
function Xn(n) {
  let t = "";
  const e = new Uint32Array(2);
  let i = new Uint16Array(n.buffer, n.byteOffset, n.byteLength / 2);
  const s = new Uint32Array((i = new Uint16Array(i).reverse()).buffer);
  let r = -1;
  const o = i.length - 1;
  do {
    for (e[0] = i[r = 0]; r < o; )
      i[r++] = e[1] = e[0] / 10, e[0] = (e[0] - e[1] * 10 << 16) + i[r];
    i[r] = e[1] = e[0] / 10, e[0] = e[0] - e[1] * 10, t = `${e[0]}${t}`;
  } while (s[0] || s[1] || s[2] || s[3]);
  return t ?? "0";
}
class wi {
  /** @nocollapse */
  static new(t, e) {
    switch (e) {
      case !0:
        return new ye(t);
      case !1:
        return new ge(t);
    }
    switch (t.constructor) {
      case Int8Array:
      case Int16Array:
      case Int32Array:
      case BigInt64Array:
        return new ye(t);
    }
    return t.byteLength === 16 ? new $e(t) : new ge(t);
  }
  /** @nocollapse */
  static signed(t) {
    return new ye(t);
  }
  /** @nocollapse */
  static unsigned(t) {
    return new ge(t);
  }
  /** @nocollapse */
  static decimal(t) {
    return new $e(t);
  }
  constructor(t, e) {
    return wi.new(t, e);
  }
}
var Js, Ks, Gs, qs, Zs, Qs, Xs, tr, er, nr, ir, sr, rr, or, ar, cr, lr, ur, dr, hr, fr, pr;
class f {
  /** @nocollapse */
  static isNull(t) {
    return t?.typeId === l.Null;
  }
  /** @nocollapse */
  static isInt(t) {
    return t?.typeId === l.Int;
  }
  /** @nocollapse */
  static isFloat(t) {
    return t?.typeId === l.Float;
  }
  /** @nocollapse */
  static isBinary(t) {
    return t?.typeId === l.Binary;
  }
  /** @nocollapse */
  static isLargeBinary(t) {
    return t?.typeId === l.LargeBinary;
  }
  /** @nocollapse */
  static isUtf8(t) {
    return t?.typeId === l.Utf8;
  }
  /** @nocollapse */
  static isLargeUtf8(t) {
    return t?.typeId === l.LargeUtf8;
  }
  /** @nocollapse */
  static isBool(t) {
    return t?.typeId === l.Bool;
  }
  /** @nocollapse */
  static isDecimal(t) {
    return t?.typeId === l.Decimal;
  }
  /** @nocollapse */
  static isDate(t) {
    return t?.typeId === l.Date;
  }
  /** @nocollapse */
  static isTime(t) {
    return t?.typeId === l.Time;
  }
  /** @nocollapse */
  static isTimestamp(t) {
    return t?.typeId === l.Timestamp;
  }
  /** @nocollapse */
  static isInterval(t) {
    return t?.typeId === l.Interval;
  }
  /** @nocollapse */
  static isDuration(t) {
    return t?.typeId === l.Duration;
  }
  /** @nocollapse */
  static isList(t) {
    return t?.typeId === l.List;
  }
  /** @nocollapse */
  static isStruct(t) {
    return t?.typeId === l.Struct;
  }
  /** @nocollapse */
  static isUnion(t) {
    return t?.typeId === l.Union;
  }
  /** @nocollapse */
  static isFixedSizeBinary(t) {
    return t?.typeId === l.FixedSizeBinary;
  }
  /** @nocollapse */
  static isFixedSizeList(t) {
    return t?.typeId === l.FixedSizeList;
  }
  /** @nocollapse */
  static isMap(t) {
    return t?.typeId === l.Map;
  }
  /** @nocollapse */
  static isDictionary(t) {
    return t?.typeId === l.Dictionary;
  }
  /** @nocollapse */
  static isDenseUnion(t) {
    return f.isUnion(t) && t.mode === X.Dense;
  }
  /** @nocollapse */
  static isSparseUnion(t) {
    return f.isUnion(t) && t.mode === X.Sparse;
  }
  constructor(t) {
    this.typeId = t;
  }
}
Js = Symbol.toStringTag;
f[Js] = ((n) => (n.children = null, n.ArrayType = Array, n.OffsetArrayType = Int32Array, n[Symbol.toStringTag] = "DataType"))(f.prototype);
class xt extends f {
  constructor() {
    super(l.Null);
  }
  toString() {
    return "Null";
  }
}
Ks = Symbol.toStringTag;
xt[Ks] = ((n) => n[Symbol.toStringTag] = "Null")(xt.prototype);
class tt extends f {
  constructor(t, e) {
    super(l.Int), this.isSigned = t, this.bitWidth = e;
  }
  get ArrayType() {
    switch (this.bitWidth) {
      case 8:
        return this.isSigned ? Int8Array : Uint8Array;
      case 16:
        return this.isSigned ? Int16Array : Uint16Array;
      case 32:
        return this.isSigned ? Int32Array : Uint32Array;
      case 64:
        return this.isSigned ? BigInt64Array : BigUint64Array;
    }
    throw new Error(`Unrecognized ${this[Symbol.toStringTag]} type`);
  }
  toString() {
    return `${this.isSigned ? "I" : "Ui"}nt${this.bitWidth}`;
  }
}
Gs = Symbol.toStringTag;
tt[Gs] = ((n) => (n.isSigned = null, n.bitWidth = null, n[Symbol.toStringTag] = "Int"))(tt.prototype);
class yr extends tt {
  constructor() {
    super(!0, 8);
  }
  get ArrayType() {
    return Int8Array;
  }
}
class gr extends tt {
  constructor() {
    super(!0, 16);
  }
  get ArrayType() {
    return Int16Array;
  }
}
class Yt extends tt {
  constructor() {
    super(!0, 32);
  }
  get ArrayType() {
    return Int32Array;
  }
}
let Ii = class extends tt {
  constructor() {
    super(!0, 64);
  }
  get ArrayType() {
    return BigInt64Array;
  }
};
class mr extends tt {
  constructor() {
    super(!1, 8);
  }
  get ArrayType() {
    return Uint8Array;
  }
}
class br extends tt {
  constructor() {
    super(!1, 16);
  }
  get ArrayType() {
    return Uint16Array;
  }
}
class _r extends tt {
  constructor() {
    super(!1, 32);
  }
  get ArrayType() {
    return Uint32Array;
  }
}
let vr = class extends tt {
  constructor() {
    super(!1, 64);
  }
  get ArrayType() {
    return BigUint64Array;
  }
};
Object.defineProperty(yr.prototype, "ArrayType", { value: Int8Array });
Object.defineProperty(gr.prototype, "ArrayType", { value: Int16Array });
Object.defineProperty(Yt.prototype, "ArrayType", { value: Int32Array });
Object.defineProperty(Ii.prototype, "ArrayType", { value: BigInt64Array });
Object.defineProperty(mr.prototype, "ArrayType", { value: Uint8Array });
Object.defineProperty(br.prototype, "ArrayType", { value: Uint16Array });
Object.defineProperty(_r.prototype, "ArrayType", { value: Uint32Array });
Object.defineProperty(vr.prototype, "ArrayType", { value: BigUint64Array });
class ve extends f {
  constructor(t) {
    super(l.Float), this.precision = t;
  }
  get ArrayType() {
    switch (this.precision) {
      case W.HALF:
        return Uint16Array;
      case W.SINGLE:
        return Float32Array;
      case W.DOUBLE:
        return Float64Array;
    }
    throw new Error(`Unrecognized ${this[Symbol.toStringTag]} type`);
  }
  toString() {
    return `Float${this.precision << 5 || 16}`;
  }
}
qs = Symbol.toStringTag;
ve[qs] = ((n) => (n.precision = null, n[Symbol.toStringTag] = "Float"))(ve.prototype);
class wr extends ve {
  constructor() {
    super(W.SINGLE);
  }
}
class jn extends ve {
  constructor() {
    super(W.DOUBLE);
  }
}
Object.defineProperty(wr.prototype, "ArrayType", { value: Float32Array });
Object.defineProperty(jn.prototype, "ArrayType", { value: Float64Array });
class vn extends f {
  constructor() {
    super(l.Binary);
  }
  toString() {
    return "Binary";
  }
}
Zs = Symbol.toStringTag;
vn[Zs] = ((n) => (n.ArrayType = Uint8Array, n[Symbol.toStringTag] = "Binary"))(vn.prototype);
class wn extends f {
  constructor() {
    super(l.LargeBinary);
  }
  toString() {
    return "LargeBinary";
  }
}
Qs = Symbol.toStringTag;
wn[Qs] = ((n) => (n.ArrayType = Uint8Array, n.OffsetArrayType = BigInt64Array, n[Symbol.toStringTag] = "LargeBinary"))(wn.prototype);
class We extends f {
  constructor() {
    super(l.Utf8);
  }
  toString() {
    return "Utf8";
  }
}
Xs = Symbol.toStringTag;
We[Xs] = ((n) => (n.ArrayType = Uint8Array, n[Symbol.toStringTag] = "Utf8"))(We.prototype);
class In extends f {
  constructor() {
    super(l.LargeUtf8);
  }
  toString() {
    return "LargeUtf8";
  }
}
tr = Symbol.toStringTag;
In[tr] = ((n) => (n.ArrayType = Uint8Array, n.OffsetArrayType = BigInt64Array, n[Symbol.toStringTag] = "LargeUtf8"))(In.prototype);
class He extends f {
  constructor() {
    super(l.Bool);
  }
  toString() {
    return "Bool";
  }
}
er = Symbol.toStringTag;
He[er] = ((n) => (n.ArrayType = Uint8Array, n[Symbol.toStringTag] = "Bool"))(He.prototype);
class Sn extends f {
  constructor(t, e, i = 128) {
    super(l.Decimal), this.scale = t, this.precision = e, this.bitWidth = i;
  }
  toString() {
    return `Decimal[${this.precision}e${this.scale > 0 ? "+" : ""}${this.scale}]`;
  }
}
nr = Symbol.toStringTag;
Sn[nr] = ((n) => (n.scale = null, n.precision = null, n.ArrayType = Uint32Array, n[Symbol.toStringTag] = "Decimal"))(Sn.prototype);
class Bn extends f {
  constructor(t) {
    super(l.Date), this.unit = t;
  }
  toString() {
    return `Date${(this.unit + 1) * 32}<${dt[this.unit]}>`;
  }
  get ArrayType() {
    return this.unit === dt.DAY ? Int32Array : BigInt64Array;
  }
}
ir = Symbol.toStringTag;
Bn[ir] = ((n) => (n.unit = null, n[Symbol.toStringTag] = "Date"))(Bn.prototype);
class An extends f {
  constructor(t, e) {
    super(l.Time), this.unit = t, this.bitWidth = e;
  }
  toString() {
    return `Time${this.bitWidth}<${b[this.unit]}>`;
  }
  get ArrayType() {
    switch (this.bitWidth) {
      case 32:
        return Int32Array;
      case 64:
        return BigInt64Array;
    }
    throw new Error(`Unrecognized ${this[Symbol.toStringTag]} type`);
  }
}
sr = Symbol.toStringTag;
An[sr] = ((n) => (n.unit = null, n.bitWidth = null, n[Symbol.toStringTag] = "Time"))(An.prototype);
class Je extends f {
  constructor(t, e) {
    super(l.Timestamp), this.unit = t, this.timezone = e;
  }
  toString() {
    return `Timestamp<${b[this.unit]}${this.timezone ? `, ${this.timezone}` : ""}>`;
  }
}
rr = Symbol.toStringTag;
Je[rr] = ((n) => (n.unit = null, n.timezone = null, n.ArrayType = BigInt64Array, n[Symbol.toStringTag] = "Timestamp"))(Je.prototype);
class Ta extends Je {
  constructor(t) {
    super(b.MILLISECOND, t);
  }
}
class Dn extends f {
  constructor(t) {
    super(l.Interval), this.unit = t;
  }
  toString() {
    return `Interval<${J[this.unit]}>`;
  }
}
or = Symbol.toStringTag;
Dn[or] = ((n) => (n.unit = null, n.ArrayType = Int32Array, n[Symbol.toStringTag] = "Interval"))(Dn.prototype);
class On extends f {
  constructor(t) {
    super(l.Duration), this.unit = t;
  }
  toString() {
    return `Duration<${b[this.unit]}>`;
  }
}
ar = Symbol.toStringTag;
On[ar] = ((n) => (n.unit = null, n.ArrayType = BigInt64Array, n[Symbol.toStringTag] = "Duration"))(On.prototype);
class we extends f {
  constructor(t) {
    super(l.List), this.children = [t];
  }
  toString() {
    return `List<${this.valueType}>`;
  }
  get valueType() {
    return this.children[0].type;
  }
  get valueField() {
    return this.children[0];
  }
  get ArrayType() {
    return this.valueType.ArrayType;
  }
}
cr = Symbol.toStringTag;
we[cr] = ((n) => (n.children = null, n[Symbol.toStringTag] = "List"))(we.prototype);
class H extends f {
  constructor(t) {
    super(l.Struct), this.children = t;
  }
  toString() {
    return `Struct<{${this.children.map((t) => `${t.name}:${t.type}`).join(", ")}}>`;
  }
}
lr = Symbol.toStringTag;
H[lr] = ((n) => (n.children = null, n[Symbol.toStringTag] = "Struct"))(H.prototype);
class Ke extends f {
  constructor(t, e, i) {
    super(l.Union), this.mode = t, this.children = i, this.typeIds = e = Int32Array.from(e), this.typeIdToChildIndex = e.reduce((s, r, o) => (s[r] = o) && s || s, /* @__PURE__ */ Object.create(null));
  }
  toString() {
    return `${this[Symbol.toStringTag]}<${this.children.map((t) => `${t.type}`).join(" | ")}>`;
  }
}
ur = Symbol.toStringTag;
Ke[ur] = ((n) => (n.mode = null, n.typeIds = null, n.children = null, n.typeIdToChildIndex = null, n.ArrayType = Int8Array, n[Symbol.toStringTag] = "Union"))(Ke.prototype);
class Fn extends f {
  constructor(t) {
    super(l.FixedSizeBinary), this.byteWidth = t;
  }
  toString() {
    return `FixedSizeBinary[${this.byteWidth}]`;
  }
}
dr = Symbol.toStringTag;
Fn[dr] = ((n) => (n.byteWidth = null, n.ArrayType = Uint8Array, n[Symbol.toStringTag] = "FixedSizeBinary"))(Fn.prototype);
class Ge extends f {
  constructor(t, e) {
    super(l.FixedSizeList), this.listSize = t, this.children = [e];
  }
  get valueType() {
    return this.children[0].type;
  }
  get valueField() {
    return this.children[0];
  }
  get ArrayType() {
    return this.valueType.ArrayType;
  }
  toString() {
    return `FixedSizeList[${this.listSize}]<${this.valueType}>`;
  }
}
hr = Symbol.toStringTag;
Ge[hr] = ((n) => (n.children = null, n.listSize = null, n[Symbol.toStringTag] = "FixedSizeList"))(Ge.prototype);
class qe extends f {
  constructor(t, e = !1) {
    var i, s, r;
    if (super(l.Map), this.children = [t], this.keysSorted = e, t && (t.name = "entries", !((i = t?.type) === null || i === void 0) && i.children)) {
      const o = (s = t?.type) === null || s === void 0 ? void 0 : s.children[0];
      o && (o.name = "key");
      const a = (r = t?.type) === null || r === void 0 ? void 0 : r.children[1];
      a && (a.name = "value");
    }
  }
  get keyType() {
    return this.children[0].type.children[0].type;
  }
  get valueType() {
    return this.children[0].type.children[1].type;
  }
  get childType() {
    return this.children[0].type;
  }
  toString() {
    return `Map<{${this.children[0].type.children.map((t) => `${t.name}:${t.type}`).join(", ")}}>`;
  }
}
fr = Symbol.toStringTag;
qe[fr] = ((n) => (n.children = null, n.keysSorted = null, n[Symbol.toStringTag] = "Map_"))(qe.prototype);
const La = /* @__PURE__ */ ((n) => () => ++n)(-1);
class Wt extends f {
  constructor(t, e, i, s) {
    super(l.Dictionary), this.indices = e, this.dictionary = t, this.isOrdered = s || !1, this.id = i == null ? La() : k(i);
  }
  get children() {
    return this.dictionary.children;
  }
  get valueType() {
    return this.dictionary;
  }
  get ArrayType() {
    return this.dictionary.ArrayType;
  }
  toString() {
    return `Dictionary<${this.indices}, ${this.dictionary}>`;
  }
}
pr = Symbol.toStringTag;
Wt[pr] = ((n) => (n.id = null, n.indices = null, n.isOrdered = null, n.dictionary = null, n[Symbol.toStringTag] = "Dictionary"))(Wt.prototype);
function It(n) {
  const t = n;
  switch (n.typeId) {
    case l.Decimal:
      return n.bitWidth / 32;
    case l.Interval:
      return t.unit === J.MONTH_DAY_NANO ? 4 : 1 + t.unit;
    // case Type.Int: return 1 + +((t as Int_).bitWidth > 32);
    // case Type.Time: return 1 + +((t as Time_).bitWidth > 32);
    case l.FixedSizeList:
      return t.listSize;
    case l.FixedSizeBinary:
      return t.byteWidth;
    default:
      return 1;
  }
}
class M {
  visitMany(t, ...e) {
    return t.map((i, s) => this.visit(i, ...e.map((r) => r[s])));
  }
  visit(...t) {
    return this.getVisitFn(t[0], !1).apply(this, t);
  }
  getVisitFn(t, e = !0) {
    return Ua(this, t, e);
  }
  getVisitFnByTypeId(t, e = !0) {
    return ue(this, t, e);
  }
  visitNull(t, ...e) {
    return null;
  }
  visitBool(t, ...e) {
    return null;
  }
  visitInt(t, ...e) {
    return null;
  }
  visitFloat(t, ...e) {
    return null;
  }
  visitUtf8(t, ...e) {
    return null;
  }
  visitLargeUtf8(t, ...e) {
    return null;
  }
  visitBinary(t, ...e) {
    return null;
  }
  visitLargeBinary(t, ...e) {
    return null;
  }
  visitFixedSizeBinary(t, ...e) {
    return null;
  }
  visitDate(t, ...e) {
    return null;
  }
  visitTimestamp(t, ...e) {
    return null;
  }
  visitTime(t, ...e) {
    return null;
  }
  visitDecimal(t, ...e) {
    return null;
  }
  visitList(t, ...e) {
    return null;
  }
  visitStruct(t, ...e) {
    return null;
  }
  visitUnion(t, ...e) {
    return null;
  }
  visitDictionary(t, ...e) {
    return null;
  }
  visitInterval(t, ...e) {
    return null;
  }
  visitDuration(t, ...e) {
    return null;
  }
  visitFixedSizeList(t, ...e) {
    return null;
  }
  visitMap(t, ...e) {
    return null;
  }
}
function Ua(n, t, e = !0) {
  return typeof t == "number" ? ue(n, t, e) : typeof t == "string" && t in l ? ue(n, l[t], e) : t && t instanceof f ? ue(n, ys(t), e) : t?.type && t.type instanceof f ? ue(n, ys(t.type), e) : ue(n, l.NONE, e);
}
function ue(n, t, e = !0) {
  let i = null;
  switch (t) {
    case l.Null:
      i = n.visitNull;
      break;
    case l.Bool:
      i = n.visitBool;
      break;
    case l.Int:
      i = n.visitInt;
      break;
    case l.Int8:
      i = n.visitInt8 || n.visitInt;
      break;
    case l.Int16:
      i = n.visitInt16 || n.visitInt;
      break;
    case l.Int32:
      i = n.visitInt32 || n.visitInt;
      break;
    case l.Int64:
      i = n.visitInt64 || n.visitInt;
      break;
    case l.Uint8:
      i = n.visitUint8 || n.visitInt;
      break;
    case l.Uint16:
      i = n.visitUint16 || n.visitInt;
      break;
    case l.Uint32:
      i = n.visitUint32 || n.visitInt;
      break;
    case l.Uint64:
      i = n.visitUint64 || n.visitInt;
      break;
    case l.Float:
      i = n.visitFloat;
      break;
    case l.Float16:
      i = n.visitFloat16 || n.visitFloat;
      break;
    case l.Float32:
      i = n.visitFloat32 || n.visitFloat;
      break;
    case l.Float64:
      i = n.visitFloat64 || n.visitFloat;
      break;
    case l.Utf8:
      i = n.visitUtf8;
      break;
    case l.LargeUtf8:
      i = n.visitLargeUtf8;
      break;
    case l.Binary:
      i = n.visitBinary;
      break;
    case l.LargeBinary:
      i = n.visitLargeBinary;
      break;
    case l.FixedSizeBinary:
      i = n.visitFixedSizeBinary;
      break;
    case l.Date:
      i = n.visitDate;
      break;
    case l.DateDay:
      i = n.visitDateDay || n.visitDate;
      break;
    case l.DateMillisecond:
      i = n.visitDateMillisecond || n.visitDate;
      break;
    case l.Timestamp:
      i = n.visitTimestamp;
      break;
    case l.TimestampSecond:
      i = n.visitTimestampSecond || n.visitTimestamp;
      break;
    case l.TimestampMillisecond:
      i = n.visitTimestampMillisecond || n.visitTimestamp;
      break;
    case l.TimestampMicrosecond:
      i = n.visitTimestampMicrosecond || n.visitTimestamp;
      break;
    case l.TimestampNanosecond:
      i = n.visitTimestampNanosecond || n.visitTimestamp;
      break;
    case l.Time:
      i = n.visitTime;
      break;
    case l.TimeSecond:
      i = n.visitTimeSecond || n.visitTime;
      break;
    case l.TimeMillisecond:
      i = n.visitTimeMillisecond || n.visitTime;
      break;
    case l.TimeMicrosecond:
      i = n.visitTimeMicrosecond || n.visitTime;
      break;
    case l.TimeNanosecond:
      i = n.visitTimeNanosecond || n.visitTime;
      break;
    case l.Decimal:
      i = n.visitDecimal;
      break;
    case l.List:
      i = n.visitList;
      break;
    case l.Struct:
      i = n.visitStruct;
      break;
    case l.Union:
      i = n.visitUnion;
      break;
    case l.DenseUnion:
      i = n.visitDenseUnion || n.visitUnion;
      break;
    case l.SparseUnion:
      i = n.visitSparseUnion || n.visitUnion;
      break;
    case l.Dictionary:
      i = n.visitDictionary;
      break;
    case l.Interval:
      i = n.visitInterval;
      break;
    case l.IntervalDayTime:
      i = n.visitIntervalDayTime || n.visitInterval;
      break;
    case l.IntervalYearMonth:
      i = n.visitIntervalYearMonth || n.visitInterval;
      break;
    case l.IntervalMonthDayNano:
      i = n.visitIntervalMonthDayNano || n.visitInterval;
      break;
    case l.Duration:
      i = n.visitDuration;
      break;
    case l.DurationSecond:
      i = n.visitDurationSecond || n.visitDuration;
      break;
    case l.DurationMillisecond:
      i = n.visitDurationMillisecond || n.visitDuration;
      break;
    case l.DurationMicrosecond:
      i = n.visitDurationMicrosecond || n.visitDuration;
      break;
    case l.DurationNanosecond:
      i = n.visitDurationNanosecond || n.visitDuration;
      break;
    case l.FixedSizeList:
      i = n.visitFixedSizeList;
      break;
    case l.Map:
      i = n.visitMap;
      break;
  }
  if (typeof i == "function")
    return i;
  if (!e)
    return () => null;
  throw new Error(`Unrecognized type '${l[t]}'`);
}
function ys(n) {
  switch (n.typeId) {
    case l.Null:
      return l.Null;
    case l.Int: {
      const { bitWidth: t, isSigned: e } = n;
      switch (t) {
        case 8:
          return e ? l.Int8 : l.Uint8;
        case 16:
          return e ? l.Int16 : l.Uint16;
        case 32:
          return e ? l.Int32 : l.Uint32;
        case 64:
          return e ? l.Int64 : l.Uint64;
      }
      return l.Int;
    }
    case l.Float:
      switch (n.precision) {
        case W.HALF:
          return l.Float16;
        case W.SINGLE:
          return l.Float32;
        case W.DOUBLE:
          return l.Float64;
      }
      return l.Float;
    case l.Binary:
      return l.Binary;
    case l.LargeBinary:
      return l.LargeBinary;
    case l.Utf8:
      return l.Utf8;
    case l.LargeUtf8:
      return l.LargeUtf8;
    case l.Bool:
      return l.Bool;
    case l.Decimal:
      return l.Decimal;
    case l.Time:
      switch (n.unit) {
        case b.SECOND:
          return l.TimeSecond;
        case b.MILLISECOND:
          return l.TimeMillisecond;
        case b.MICROSECOND:
          return l.TimeMicrosecond;
        case b.NANOSECOND:
          return l.TimeNanosecond;
      }
      return l.Time;
    case l.Timestamp:
      switch (n.unit) {
        case b.SECOND:
          return l.TimestampSecond;
        case b.MILLISECOND:
          return l.TimestampMillisecond;
        case b.MICROSECOND:
          return l.TimestampMicrosecond;
        case b.NANOSECOND:
          return l.TimestampNanosecond;
      }
      return l.Timestamp;
    case l.Date:
      switch (n.unit) {
        case dt.DAY:
          return l.DateDay;
        case dt.MILLISECOND:
          return l.DateMillisecond;
      }
      return l.Date;
    case l.Interval:
      switch (n.unit) {
        case J.DAY_TIME:
          return l.IntervalDayTime;
        case J.YEAR_MONTH:
          return l.IntervalYearMonth;
        case J.MONTH_DAY_NANO:
          return l.IntervalMonthDayNano;
      }
      return l.Interval;
    case l.Duration:
      switch (n.unit) {
        case b.SECOND:
          return l.DurationSecond;
        case b.MILLISECOND:
          return l.DurationMillisecond;
        case b.MICROSECOND:
          return l.DurationMicrosecond;
        case b.NANOSECOND:
          return l.DurationNanosecond;
      }
      return l.Duration;
    case l.Map:
      return l.Map;
    case l.List:
      return l.List;
    case l.Struct:
      return l.Struct;
    case l.Union:
      switch (n.mode) {
        case X.Dense:
          return l.DenseUnion;
        case X.Sparse:
          return l.SparseUnion;
      }
      return l.Union;
    case l.FixedSizeBinary:
      return l.FixedSizeBinary;
    case l.FixedSizeList:
      return l.FixedSizeList;
    case l.Dictionary:
      return l.Dictionary;
  }
  throw new Error(`Unrecognized type '${l[n.typeId]}'`);
}
M.prototype.visitInt8 = null;
M.prototype.visitInt16 = null;
M.prototype.visitInt32 = null;
M.prototype.visitInt64 = null;
M.prototype.visitUint8 = null;
M.prototype.visitUint16 = null;
M.prototype.visitUint32 = null;
M.prototype.visitUint64 = null;
M.prototype.visitFloat16 = null;
M.prototype.visitFloat32 = null;
M.prototype.visitFloat64 = null;
M.prototype.visitDateDay = null;
M.prototype.visitDateMillisecond = null;
M.prototype.visitTimestampSecond = null;
M.prototype.visitTimestampMillisecond = null;
M.prototype.visitTimestampMicrosecond = null;
M.prototype.visitTimestampNanosecond = null;
M.prototype.visitTimeSecond = null;
M.prototype.visitTimeMillisecond = null;
M.prototype.visitTimeMicrosecond = null;
M.prototype.visitTimeNanosecond = null;
M.prototype.visitDenseUnion = null;
M.prototype.visitSparseUnion = null;
M.prototype.visitIntervalDayTime = null;
M.prototype.visitIntervalYearMonth = null;
M.prototype.visitIntervalMonthDayNano = null;
M.prototype.visitDuration = null;
M.prototype.visitDurationSecond = null;
M.prototype.visitDurationMillisecond = null;
M.prototype.visitDurationMicrosecond = null;
M.prototype.visitDurationNanosecond = null;
const Ir = new Float64Array(1), ne = new Uint32Array(Ir.buffer);
function Sr(n) {
  const t = (n & 31744) >> 10, e = (n & 1023) / 1024, i = Math.pow(-1, (n & 32768) >> 15);
  switch (t) {
    case 31:
      return i * (e ? Number.NaN : 1 / 0);
    case 0:
      return i * (e ? 6103515625e-14 * e : 0);
  }
  return i * Math.pow(2, t - 15) * (1 + e);
}
function Br(n) {
  if (n !== n)
    return 32256;
  Ir[0] = n;
  const t = (ne[1] & 2147483648) >> 16 & 65535;
  let e = ne[1] & 2146435072, i = 0;
  return e >= 1089470464 ? ne[0] > 0 ? e = 31744 : (e = (e & 2080374784) >> 16, i = (ne[1] & 1048575) >> 10) : e <= 1056964608 ? (i = 1048576 + (ne[1] & 1048575), i = 1048576 + (i << (e >> 20) - 998) >> 21, e = 0) : (e = e - 1056964608 >> 10, i = (ne[1] & 1048575) + 512 >> 10), t | e | i & 65535;
}
class _ extends M {
}
function S(n) {
  return (t, e, i) => {
    if (t.setValid(e, i != null))
      return n(t, e, i);
  };
}
const xa = (n, t, e) => {
  n[t] = Math.floor(e / 864e5);
}, Ar = (n, t, e, i) => {
  if (e + 1 < t.length) {
    const s = k(t[e]), r = k(t[e + 1]);
    n.set(i.subarray(0, r - s), s);
  }
}, Ca = ({ offset: n, values: t }, e, i) => {
  const s = n + e;
  i ? t[s >> 3] |= 1 << s % 8 : t[s >> 3] &= ~(1 << s % 8);
}, Et = ({ values: n }, t, e) => {
  n[t] = e;
}, Si = ({ values: n }, t, e) => {
  n[t] = e;
}, Dr = ({ values: n }, t, e) => {
  n[t] = Br(e);
}, Ea = (n, t, e) => {
  switch (n.type.precision) {
    case W.HALF:
      return Dr(n, t, e);
    case W.SINGLE:
    case W.DOUBLE:
      return Si(n, t, e);
  }
}, Bi = ({ values: n }, t, e) => {
  xa(n, t, e.valueOf());
}, Ai = ({ values: n }, t, e) => {
  n[t] = BigInt(e);
}, Or = ({ stride: n, values: t }, e, i) => {
  t.set(i.subarray(0, n), n * e);
}, Fr = ({ values: n, valueOffsets: t }, e, i) => Ar(n, t, e, i), Mr = ({ values: n, valueOffsets: t }, e, i) => Ar(n, t, e, Ze(i)), Nr = (n, t, e) => {
  n.type.unit === dt.DAY ? Bi(n, t, e) : Ai(n, t, e);
}, Di = ({ values: n }, t, e) => {
  n[t] = BigInt(e / 1e3);
}, Oi = ({ values: n }, t, e) => {
  n[t] = BigInt(e);
}, Fi = ({ values: n }, t, e) => {
  n[t] = BigInt(e * 1e3);
}, Mi = ({ values: n }, t, e) => {
  n[t] = BigInt(e * 1e6);
}, Tr = (n, t, e) => {
  switch (n.type.unit) {
    case b.SECOND:
      return Di(n, t, e);
    case b.MILLISECOND:
      return Oi(n, t, e);
    case b.MICROSECOND:
      return Fi(n, t, e);
    case b.NANOSECOND:
      return Mi(n, t, e);
  }
}, Ni = ({ values: n }, t, e) => {
  n[t] = e;
}, Ti = ({ values: n }, t, e) => {
  n[t] = e;
}, Li = ({ values: n }, t, e) => {
  n[t] = e;
}, Ui = ({ values: n }, t, e) => {
  n[t] = e;
}, Lr = (n, t, e) => {
  switch (n.type.unit) {
    case b.SECOND:
      return Ni(n, t, e);
    case b.MILLISECOND:
      return Ti(n, t, e);
    case b.MICROSECOND:
      return Li(n, t, e);
    case b.NANOSECOND:
      return Ui(n, t, e);
  }
}, Ur = ({ values: n, stride: t }, e, i) => {
  n.set(i.subarray(0, t), t * e);
}, Va = (n, t, e) => {
  const i = n.children[0], s = n.valueOffsets, r = ht.getVisitFn(i);
  if (Array.isArray(e))
    for (let o = -1, a = s[t], c = s[t + 1]; a < c; )
      r(i, a++, e[++o]);
  else
    for (let o = -1, a = s[t], c = s[t + 1]; a < c; )
      r(i, a++, e.get(++o));
}, Ra = (n, t, e) => {
  const i = n.children[0], { valueOffsets: s } = n, r = ht.getVisitFn(i);
  let { [t]: o, [t + 1]: a } = s;
  const c = e instanceof Map ? e.entries() : Object.entries(e);
  for (const u of c)
    if (r(i, o, u), ++o >= a)
      break;
}, za = (n, t) => (e, i, s, r) => i && e(i, n, t[r]), ka = (n, t) => (e, i, s, r) => i && e(i, n, t.get(r)), Pa = (n, t) => (e, i, s, r) => i && e(i, n, t.get(s.name)), ja = (n, t) => (e, i, s, r) => i && e(i, n, t[s.name]), $a = (n, t, e) => {
  const i = n.type.children.map((r) => ht.getVisitFn(r.type)), s = e instanceof Map ? Pa(t, e) : e instanceof A ? ka(t, e) : Array.isArray(e) ? za(t, e) : ja(t, e);
  n.type.children.forEach((r, o) => s(i[o], n.children[o], r, o));
}, Ya = (n, t, e) => {
  n.type.mode === X.Dense ? xr(n, t, e) : Cr(n, t, e);
}, xr = (n, t, e) => {
  const i = n.type.typeIdToChildIndex[n.typeIds[t]], s = n.children[i];
  ht.visit(s, n.valueOffsets[t], e);
}, Cr = (n, t, e) => {
  const i = n.type.typeIdToChildIndex[n.typeIds[t]], s = n.children[i];
  ht.visit(s, t, e);
}, Wa = (n, t, e) => {
  var i;
  (i = n.dictionary) === null || i === void 0 || i.set(n.values[t], e);
}, Er = (n, t, e) => {
  switch (n.type.unit) {
    case J.YEAR_MONTH:
      return Ci(n, t, e);
    case J.DAY_TIME:
      return xi(n, t, e);
    case J.MONTH_DAY_NANO:
      return Ei(n, t, e);
  }
}, xi = ({ values: n }, t, e) => {
  n.set(e.subarray(0, 2), 2 * t);
}, Ci = ({ values: n }, t, e) => {
  n[t] = e[0] * 12 + e[1] % 12;
}, Ei = ({ values: n, stride: t }, e, i) => {
  n.set(i.subarray(0, t), t * e);
}, Vi = ({ values: n }, t, e) => {
  n[t] = e;
}, Ri = ({ values: n }, t, e) => {
  n[t] = e;
}, zi = ({ values: n }, t, e) => {
  n[t] = e;
}, ki = ({ values: n }, t, e) => {
  n[t] = e;
}, Vr = (n, t, e) => {
  switch (n.type.unit) {
    case b.SECOND:
      return Vi(n, t, e);
    case b.MILLISECOND:
      return Ri(n, t, e);
    case b.MICROSECOND:
      return zi(n, t, e);
    case b.NANOSECOND:
      return ki(n, t, e);
  }
}, Ha = (n, t, e) => {
  const { stride: i } = n, s = n.children[0], r = ht.getVisitFn(s);
  if (Array.isArray(e))
    for (let o = -1, a = t * i; ++o < i; )
      r(s, a + o, e[o]);
  else
    for (let o = -1, a = t * i; ++o < i; )
      r(s, a + o, e.get(o));
};
_.prototype.visitBool = S(Ca);
_.prototype.visitInt = S(Et);
_.prototype.visitInt8 = S(Et);
_.prototype.visitInt16 = S(Et);
_.prototype.visitInt32 = S(Et);
_.prototype.visitInt64 = S(Et);
_.prototype.visitUint8 = S(Et);
_.prototype.visitUint16 = S(Et);
_.prototype.visitUint32 = S(Et);
_.prototype.visitUint64 = S(Et);
_.prototype.visitFloat = S(Ea);
_.prototype.visitFloat16 = S(Dr);
_.prototype.visitFloat32 = S(Si);
_.prototype.visitFloat64 = S(Si);
_.prototype.visitUtf8 = S(Mr);
_.prototype.visitLargeUtf8 = S(Mr);
_.prototype.visitBinary = S(Fr);
_.prototype.visitLargeBinary = S(Fr);
_.prototype.visitFixedSizeBinary = S(Or);
_.prototype.visitDate = S(Nr);
_.prototype.visitDateDay = S(Bi);
_.prototype.visitDateMillisecond = S(Ai);
_.prototype.visitTimestamp = S(Tr);
_.prototype.visitTimestampSecond = S(Di);
_.prototype.visitTimestampMillisecond = S(Oi);
_.prototype.visitTimestampMicrosecond = S(Fi);
_.prototype.visitTimestampNanosecond = S(Mi);
_.prototype.visitTime = S(Lr);
_.prototype.visitTimeSecond = S(Ni);
_.prototype.visitTimeMillisecond = S(Ti);
_.prototype.visitTimeMicrosecond = S(Li);
_.prototype.visitTimeNanosecond = S(Ui);
_.prototype.visitDecimal = S(Ur);
_.prototype.visitList = S(Va);
_.prototype.visitStruct = S($a);
_.prototype.visitUnion = S(Ya);
_.prototype.visitDenseUnion = S(xr);
_.prototype.visitSparseUnion = S(Cr);
_.prototype.visitDictionary = S(Wa);
_.prototype.visitInterval = S(Er);
_.prototype.visitIntervalDayTime = S(xi);
_.prototype.visitIntervalYearMonth = S(Ci);
_.prototype.visitIntervalMonthDayNano = S(Ei);
_.prototype.visitDuration = S(Vr);
_.prototype.visitDurationSecond = S(Vi);
_.prototype.visitDurationMillisecond = S(Ri);
_.prototype.visitDurationMicrosecond = S(zi);
_.prototype.visitDurationNanosecond = S(ki);
_.prototype.visitFixedSizeList = S(Ha);
_.prototype.visitMap = S(Ra);
const ht = new _(), ft = /* @__PURE__ */ Symbol.for("parent"), me = /* @__PURE__ */ Symbol.for("rowIndex");
class Pi {
  constructor(t, e) {
    return this[ft] = t, this[me] = e, new Proxy(this, Ga);
  }
  toArray() {
    return Object.values(this.toJSON());
  }
  toJSON() {
    const t = this[me], e = this[ft], i = e.type.children, s = {};
    for (let r = -1, o = i.length; ++r < o; )
      s[i[r].name] = et.visit(e.children[r], t);
    return s;
  }
  toString() {
    return `{${[...this].map(([t, e]) => `${te(t)}: ${te(e)}`).join(", ")}}`;
  }
  [/* @__PURE__ */ Symbol.for("nodejs.util.inspect.custom")]() {
    return this.toString();
  }
  [Symbol.iterator]() {
    return new Ja(this[ft], this[me]);
  }
}
class Ja {
  constructor(t, e) {
    this.childIndex = 0, this.children = t.children, this.rowIndex = e, this.childFields = t.type.children, this.numChildren = this.childFields.length;
  }
  [Symbol.iterator]() {
    return this;
  }
  next() {
    const t = this.childIndex;
    return t < this.numChildren ? (this.childIndex = t + 1, {
      done: !1,
      value: [
        this.childFields[t].name,
        et.visit(this.children[t], this.rowIndex)
      ]
    }) : { done: !0, value: null };
  }
}
Object.defineProperties(Pi.prototype, {
  [Symbol.toStringTag]: { enumerable: !1, configurable: !1, value: "Row" },
  [ft]: { writable: !0, enumerable: !1, configurable: !1, value: null },
  [me]: { writable: !0, enumerable: !1, configurable: !1, value: -1 }
});
class Ka {
  isExtensible() {
    return !1;
  }
  deleteProperty() {
    return !1;
  }
  preventExtensions() {
    return !0;
  }
  ownKeys(t) {
    return t[ft].type.children.map((e) => e.name);
  }
  has(t, e) {
    return t[ft].type.children.some((i) => i.name === e);
  }
  getOwnPropertyDescriptor(t, e) {
    if (t[ft].type.children.some((i) => i.name === e))
      return { writable: !0, enumerable: !0, configurable: !0 };
  }
  get(t, e) {
    if (Reflect.has(t, e))
      return t[e];
    const i = t[ft].type.children.findIndex((s) => s.name === e);
    if (i !== -1) {
      const s = et.visit(t[ft].children[i], t[me]);
      return Reflect.set(t, e, s), s;
    }
  }
  set(t, e, i) {
    const s = t[ft].type.children.findIndex((r) => r.name === e);
    return s !== -1 ? (ht.visit(t[ft].children[s], t[me], i), Reflect.set(t, e, i)) : Reflect.has(t, e) || typeof e == "symbol" ? Reflect.set(t, e, i) : !1;
  }
}
const Ga = new Ka();
class p extends M {
}
function v(n) {
  return (t, e) => t.getValid(e) ? n(t, e) : null;
}
const qa = (n, t) => 864e5 * n[t], Za = (n, t) => null, Rr = (n, t, e) => {
  if (e + 1 >= t.length)
    return null;
  const i = k(t[e]), s = k(t[e + 1]);
  return n.subarray(i, s);
}, Qa = ({ offset: n, values: t }, e) => {
  const i = n + e;
  return (t[i >> 3] & 1 << i % 8) !== 0;
}, zr = ({ values: n }, t) => qa(n, t), kr = ({ values: n }, t) => k(n[t]), Ht = ({ stride: n, values: t }, e) => t[n * e], Xa = ({ stride: n, values: t }, e) => Sr(t[n * e]), Pr = ({ values: n }, t) => n[t], tc = ({ stride: n, values: t }, e) => t.subarray(n * e, n * (e + 1)), jr = ({ values: n, valueOffsets: t }, e) => Rr(n, t, e), $r = ({ values: n, valueOffsets: t }, e) => {
  const i = Rr(n, t, e);
  return i !== null ? ri(i) : null;
}, ec = ({ values: n }, t) => n[t], nc = ({ type: n, values: t }, e) => n.precision !== W.HALF ? t[e] : Sr(t[e]), ic = (n, t) => n.type.unit === dt.DAY ? zr(n, t) : kr(n, t), Yr = ({ values: n }, t) => 1e3 * k(n[t]), Wr = ({ values: n }, t) => k(n[t]), Hr = ({ values: n }, t) => Ws(n[t], BigInt(1e3)), Jr = ({ values: n }, t) => Ws(n[t], BigInt(1e6)), sc = (n, t) => {
  switch (n.type.unit) {
    case b.SECOND:
      return Yr(n, t);
    case b.MILLISECOND:
      return Wr(n, t);
    case b.MICROSECOND:
      return Hr(n, t);
    case b.NANOSECOND:
      return Jr(n, t);
  }
}, Kr = ({ values: n }, t) => n[t], Gr = ({ values: n }, t) => n[t], qr = ({ values: n }, t) => n[t], Zr = ({ values: n }, t) => n[t], rc = (n, t) => {
  switch (n.type.unit) {
    case b.SECOND:
      return Kr(n, t);
    case b.MILLISECOND:
      return Gr(n, t);
    case b.MICROSECOND:
      return qr(n, t);
    case b.NANOSECOND:
      return Zr(n, t);
  }
}, oc = ({ values: n, stride: t }, e) => wi.decimal(n.subarray(t * e, t * (e + 1))), ac = (n, t) => {
  const { valueOffsets: e, stride: i, children: s } = n, { [t * i]: r, [t * i + 1]: o } = e, c = s[0].slice(r, o - r);
  return new A([c]);
}, cc = (n, t) => {
  const { valueOffsets: e, children: i } = n, { [t]: s, [t + 1]: r } = e, o = i[0];
  return new $n(o.slice(s, r - s));
}, lc = (n, t) => new Pi(n, t), uc = (n, t) => n.type.mode === X.Dense ? Qr(n, t) : Xr(n, t), Qr = (n, t) => {
  const e = n.type.typeIdToChildIndex[n.typeIds[t]], i = n.children[e];
  return et.visit(i, n.valueOffsets[t]);
}, Xr = (n, t) => {
  const e = n.type.typeIdToChildIndex[n.typeIds[t]], i = n.children[e];
  return et.visit(i, t);
}, dc = (n, t) => {
  var e;
  return (e = n.dictionary) === null || e === void 0 ? void 0 : e.get(n.values[t]);
}, hc = (n, t) => n.type.unit === J.MONTH_DAY_NANO ? no(n, t) : n.type.unit === J.DAY_TIME ? to(n, t) : eo(n, t), to = ({ values: n }, t) => n.subarray(2 * t, 2 * (t + 1)), eo = ({ values: n }, t) => {
  const e = n[t], i = new Int32Array(2);
  return i[0] = Math.trunc(e / 12), i[1] = Math.trunc(e % 12), i;
}, no = ({ values: n }, t) => n.subarray(4 * t, 4 * (t + 1)), io = ({ values: n }, t) => n[t], so = ({ values: n }, t) => n[t], ro = ({ values: n }, t) => n[t], oo = ({ values: n }, t) => n[t], fc = (n, t) => {
  switch (n.type.unit) {
    case b.SECOND:
      return io(n, t);
    case b.MILLISECOND:
      return so(n, t);
    case b.MICROSECOND:
      return ro(n, t);
    case b.NANOSECOND:
      return oo(n, t);
  }
}, pc = (n, t) => {
  const { stride: e, children: i } = n, r = i[0].slice(t * e, e);
  return new A([r]);
};
p.prototype.visitNull = v(Za);
p.prototype.visitBool = v(Qa);
p.prototype.visitInt = v(ec);
p.prototype.visitInt8 = v(Ht);
p.prototype.visitInt16 = v(Ht);
p.prototype.visitInt32 = v(Ht);
p.prototype.visitInt64 = v(Pr);
p.prototype.visitUint8 = v(Ht);
p.prototype.visitUint16 = v(Ht);
p.prototype.visitUint32 = v(Ht);
p.prototype.visitUint64 = v(Pr);
p.prototype.visitFloat = v(nc);
p.prototype.visitFloat16 = v(Xa);
p.prototype.visitFloat32 = v(Ht);
p.prototype.visitFloat64 = v(Ht);
p.prototype.visitUtf8 = v($r);
p.prototype.visitLargeUtf8 = v($r);
p.prototype.visitBinary = v(jr);
p.prototype.visitLargeBinary = v(jr);
p.prototype.visitFixedSizeBinary = v(tc);
p.prototype.visitDate = v(ic);
p.prototype.visitDateDay = v(zr);
p.prototype.visitDateMillisecond = v(kr);
p.prototype.visitTimestamp = v(sc);
p.prototype.visitTimestampSecond = v(Yr);
p.prototype.visitTimestampMillisecond = v(Wr);
p.prototype.visitTimestampMicrosecond = v(Hr);
p.prototype.visitTimestampNanosecond = v(Jr);
p.prototype.visitTime = v(rc);
p.prototype.visitTimeSecond = v(Kr);
p.prototype.visitTimeMillisecond = v(Gr);
p.prototype.visitTimeMicrosecond = v(qr);
p.prototype.visitTimeNanosecond = v(Zr);
p.prototype.visitDecimal = v(oc);
p.prototype.visitList = v(ac);
p.prototype.visitStruct = v(lc);
p.prototype.visitUnion = v(uc);
p.prototype.visitDenseUnion = v(Qr);
p.prototype.visitSparseUnion = v(Xr);
p.prototype.visitDictionary = v(dc);
p.prototype.visitInterval = v(hc);
p.prototype.visitIntervalDayTime = v(to);
p.prototype.visitIntervalYearMonth = v(eo);
p.prototype.visitIntervalMonthDayNano = v(no);
p.prototype.visitDuration = v(fc);
p.prototype.visitDurationSecond = v(io);
p.prototype.visitDurationMillisecond = v(so);
p.prototype.visitDurationMicrosecond = v(ro);
p.prototype.visitDurationNanosecond = v(oo);
p.prototype.visitFixedSizeList = v(pc);
p.prototype.visitMap = v(cc);
const et = new p(), qt = /* @__PURE__ */ Symbol.for("keys"), be = /* @__PURE__ */ Symbol.for("vals"), de = /* @__PURE__ */ Symbol.for("kKeysAsStrings"), hi = /* @__PURE__ */ Symbol.for("_kKeysAsStrings");
class $n {
  constructor(t) {
    return this[qt] = new A([t.children[0]]).memoize(), this[be] = t.children[1], new Proxy(this, new gc());
  }
  /** @ignore */
  get [de]() {
    return this[hi] || (this[hi] = Array.from(this[qt].toArray(), String));
  }
  [Symbol.iterator]() {
    return new yc(this[qt], this[be]);
  }
  get size() {
    return this[qt].length;
  }
  toArray() {
    return Object.values(this.toJSON());
  }
  toJSON() {
    const t = this[qt], e = this[be], i = {};
    for (let s = -1, r = t.length; ++s < r; )
      i[t.get(s)] = et.visit(e, s);
    return i;
  }
  toString() {
    return `{${[...this].map(([t, e]) => `${te(t)}: ${te(e)}`).join(", ")}}`;
  }
  [/* @__PURE__ */ Symbol.for("nodejs.util.inspect.custom")]() {
    return this.toString();
  }
}
class yc {
  constructor(t, e) {
    this.keys = t, this.vals = e, this.keyIndex = 0, this.numKeys = t.length;
  }
  [Symbol.iterator]() {
    return this;
  }
  next() {
    const t = this.keyIndex;
    return t === this.numKeys ? { done: !0, value: null } : (this.keyIndex++, {
      done: !1,
      value: [
        this.keys.get(t),
        et.visit(this.vals, t)
      ]
    });
  }
}
class gc {
  isExtensible() {
    return !1;
  }
  deleteProperty() {
    return !1;
  }
  preventExtensions() {
    return !0;
  }
  ownKeys(t) {
    return t[de];
  }
  has(t, e) {
    return t[de].includes(e);
  }
  getOwnPropertyDescriptor(t, e) {
    if (t[de].indexOf(e) !== -1)
      return { writable: !0, enumerable: !0, configurable: !0 };
  }
  get(t, e) {
    if (Reflect.has(t, e))
      return t[e];
    const i = t[de].indexOf(e);
    if (i !== -1) {
      const s = et.visit(Reflect.get(t, be), i);
      return Reflect.set(t, e, s), s;
    }
  }
  set(t, e, i) {
    const s = t[de].indexOf(e);
    return s !== -1 ? (ht.visit(Reflect.get(t, be), s, i), Reflect.set(t, e, i)) : Reflect.has(t, e) ? Reflect.set(t, e, i) : !1;
  }
}
Object.defineProperties($n.prototype, {
  [Symbol.toStringTag]: { enumerable: !1, configurable: !1, value: "Row" },
  [qt]: { writable: !0, enumerable: !1, configurable: !1, value: null },
  [be]: { writable: !0, enumerable: !1, configurable: !1, value: null },
  [hi]: { writable: !0, enumerable: !1, configurable: !1, value: null }
});
let gs;
function ao(n, t, e, i) {
  const { length: s = 0 } = n;
  let r = typeof t != "number" ? 0 : t, o = typeof e != "number" ? s : e;
  return r < 0 && (r = (r % s + s) % s), o < 0 && (o = (o % s + s) % s), o < r && (gs = r, r = o, o = gs), o > s && (o = s), i ? i(n, r, o) : [r, o];
}
const ji = (n, t) => n < 0 ? t + n : n, ms = (n) => n !== n;
function Ae(n) {
  if (typeof n !== "object" || n === null)
    return ms(n) ? ms : (e) => e === n;
  if (n instanceof Date) {
    const e = n.valueOf();
    return (i) => i instanceof Date ? i.valueOf() === e : !1;
  }
  return ArrayBuffer.isView(n) ? (e) => e ? va(n, e) : !1 : n instanceof Map ? bc(n) : Array.isArray(n) ? mc(n) : n instanceof A ? _c(n) : vc(n, !0);
}
function mc(n) {
  const t = [];
  for (let e = -1, i = n.length; ++e < i; )
    t[e] = Ae(n[e]);
  return Yn(t);
}
function bc(n) {
  let t = -1;
  const e = [];
  for (const i of n.values())
    e[++t] = Ae(i);
  return Yn(e);
}
function _c(n) {
  const t = [];
  for (let e = -1, i = n.length; ++e < i; )
    t[e] = Ae(n.get(e));
  return Yn(t);
}
function vc(n, t = !1) {
  const e = Object.keys(n);
  if (!t && e.length === 0)
    return () => !1;
  const i = [];
  for (let s = -1, r = e.length; ++s < r; )
    i[s] = Ae(n[e[s]]);
  return Yn(i, e);
}
function Yn(n, t) {
  return (e) => {
    if (!e || typeof e != "object")
      return !1;
    switch (e.constructor) {
      case Array:
        return wc(n, e);
      case Map:
        return bs(n, e, e.keys());
      case $n:
      case Pi:
      case Object:
      case void 0:
        return bs(n, e, t || Object.keys(e));
    }
    return e instanceof A ? Ic(n, e) : !1;
  };
}
function wc(n, t) {
  const e = n.length;
  if (t.length !== e)
    return !1;
  for (let i = -1; ++i < e; )
    if (!n[i](t[i]))
      return !1;
  return !0;
}
function Ic(n, t) {
  const e = n.length;
  if (t.length !== e)
    return !1;
  for (let i = -1; ++i < e; )
    if (!n[i](t.get(i)))
      return !1;
  return !0;
}
function bs(n, t, e) {
  const i = e[Symbol.iterator](), s = t instanceof Map ? t.keys() : Object.keys(t)[Symbol.iterator](), r = t instanceof Map ? t.values() : Object.values(t)[Symbol.iterator]();
  let o = 0;
  const a = n.length;
  let c = r.next(), u = i.next(), d = s.next();
  for (; o < a && !u.done && !d.done && !c.done && !(u.value !== d.value || !n[o](c.value)); ++o, u = i.next(), d = s.next(), c = r.next())
    ;
  return o === a && u.done && d.done && c.done ? !0 : (i.return && i.return(), s.return && s.return(), r.return && r.return(), !1);
}
function co(n, t, e, i) {
  return (e & 1 << i) !== 0;
}
function Sc(n, t, e, i) {
  return (e & 1 << i) >> i;
}
function _s(n, t, e) {
  const i = e.byteLength + 7 & -8;
  if (n > 0 || e.byteLength < i) {
    const s = new Uint8Array(i);
    return s.set(n % 8 === 0 ? e.subarray(n >> 3) : (
      // Otherwise iterate each bit from the offset and return a new one
      fi(new $i(e, n, t, null, co)).subarray(0, i)
    )), s;
  }
  return e;
}
function fi(n) {
  const t = [];
  let e = 0, i = 0, s = 0;
  for (const o of n)
    o && (s |= 1 << i), ++i === 8 && (t[e++] = s, s = i = 0);
  (e === 0 || i > 0) && (t[e++] = s);
  const r = new Uint8Array(t.length + 7 & -8);
  return r.set(t), r;
}
class $i {
  constructor(t, e, i, s, r) {
    this.bytes = t, this.length = i, this.context = s, this.get = r, this.bit = e % 8, this.byteIndex = e >> 3, this.byte = t[this.byteIndex++], this.index = 0;
  }
  next() {
    return this.index < this.length ? (this.bit === 8 && (this.bit = 0, this.byte = this.bytes[this.byteIndex++]), {
      value: this.get(this.context, this.index++, this.byte, this.bit++)
    }) : { done: !0, value: null };
  }
  [Symbol.iterator]() {
    return this;
  }
}
function pi(n, t, e) {
  if (e - t <= 0)
    return 0;
  if (e - t < 8) {
    let r = 0;
    for (const o of new $i(n, t, e - t, n, Sc))
      r += o;
    return r;
  }
  const i = e >> 3 << 3, s = t + (t % 8 === 0 ? 0 : 8 - t % 8);
  return (
    // Get the popcnt of bits between the left hand side, and the next highest multiple of 8
    pi(n, t, s) + // Get the popcnt of bits between the right hand side, and the next lowest multiple of 8
    pi(n, i, e) + // Get the popcnt of all bits between the left and right hand sides' multiples of 8
    Bc(n, s >> 3, i - s >> 3)
  );
}
function Bc(n, t, e) {
  let i = 0, s = Math.trunc(t);
  const r = new DataView(n.buffer, n.byteOffset, n.byteLength), o = e === void 0 ? n.byteLength : s + e;
  for (; o - s >= 4; )
    i += ti(r.getUint32(s)), s += 4;
  for (; o - s >= 2; )
    i += ti(r.getUint16(s)), s += 2;
  for (; o - s >= 1; )
    i += ti(r.getUint8(s)), s += 1;
  return i;
}
function ti(n) {
  let t = Math.trunc(n);
  return t = t - (t >>> 1 & 1431655765), t = (t & 858993459) + (t >>> 2 & 858993459), (t + (t >>> 4) & 252645135) * 16843009 >>> 24;
}
const Ac = -1;
class L {
  get typeId() {
    return this.type.typeId;
  }
  get ArrayType() {
    return this.type.ArrayType;
  }
  get buffers() {
    return [this.valueOffsets, this.values, this.nullBitmap, this.typeIds];
  }
  get nullable() {
    if (this._nullCount !== 0) {
      const { type: t } = this;
      return f.isSparseUnion(t) ? this.children.some((e) => e.nullable) : f.isDenseUnion(t) ? this.children.some((e) => e.nullable) : this.nullBitmap && this.nullBitmap.byteLength > 0;
    }
    return !0;
  }
  get byteLength() {
    let t = 0;
    const { valueOffsets: e, values: i, nullBitmap: s, typeIds: r } = this;
    return e && (t += e.byteLength), i && (t += i.byteLength), s && (t += s.byteLength), r && (t += r.byteLength), this.children.reduce((o, a) => o + a.byteLength, t);
  }
  get nullCount() {
    if (f.isUnion(this.type))
      return this.children.reduce((i, s) => i + s.nullCount, 0);
    let t = this._nullCount, e;
    return t <= Ac && (e = this.nullBitmap) && (this._nullCount = t = e.length === 0 ? (
      // no null bitmap, so all values are valid
      0
    ) : this.length - pi(e, this.offset, this.offset + this.length)), t;
  }
  constructor(t, e, i, s, r, o = [], a) {
    this.type = t, this.children = o, this.dictionary = a, this.offset = Math.floor(Math.max(e || 0, 0)), this.length = Math.floor(Math.max(i || 0, 0)), this._nullCount = Math.floor(Math.max(s || 0, -1));
    let c;
    r instanceof L ? (this.stride = r.stride, this.values = r.values, this.typeIds = r.typeIds, this.nullBitmap = r.nullBitmap, this.valueOffsets = r.valueOffsets) : (this.stride = It(t), r && ((c = r[0]) && (this.valueOffsets = c), (c = r[1]) && (this.values = c), (c = r[2]) && (this.nullBitmap = c), (c = r[3]) && (this.typeIds = c)));
  }
  getValid(t) {
    const { type: e } = this;
    if (f.isUnion(e)) {
      const i = e, s = this.children[i.typeIdToChildIndex[this.typeIds[t]]], r = i.mode === X.Dense ? this.valueOffsets[t] : t;
      return s.getValid(r);
    }
    if (this.nullable && this.nullCount > 0) {
      const i = this.offset + t;
      return (this.nullBitmap[i >> 3] & 1 << i % 8) !== 0;
    }
    return !0;
  }
  setValid(t, e) {
    let i;
    const { type: s } = this;
    if (f.isUnion(s)) {
      const r = s, o = this.children[r.typeIdToChildIndex[this.typeIds[t]]], a = r.mode === X.Dense ? this.valueOffsets[t] : t;
      i = o.getValid(a), o.setValid(a, e);
    } else {
      let { nullBitmap: r } = this;
      const { offset: o, length: a } = this, c = o + t, u = 1 << c % 8, d = c >> 3;
      (!r || r.byteLength <= d) && (r = new Uint8Array((o + a + 63 & -64) >> 3).fill(255), this.nullCount > 0 ? (r.set(_s(o, a, this.nullBitmap), 0), Object.assign(this, { nullBitmap: r })) : Object.assign(this, { nullBitmap: r, _nullCount: 0 }));
      const h = r[d];
      i = (h & u) !== 0, r[d] = e ? h | u : h & ~u;
    }
    return i !== !!e && (this._nullCount = this.nullCount + (e ? -1 : 1)), e;
  }
  clone(t = this.type, e = this.offset, i = this.length, s = this._nullCount, r = this, o = this.children) {
    return new L(t, e, i, s, r, o, this.dictionary);
  }
  slice(t, e) {
    const { stride: i, typeId: s, children: r } = this, o = +(this._nullCount === 0) - 1, a = s === 16 ? i : 1, c = this._sliceBuffers(t, e, i, s);
    return this.clone(
      this.type,
      this.offset + t,
      e,
      o,
      c,
      // Don't slice children if we have value offsets (the variable-width types)
      r.length === 0 || this.valueOffsets ? r : this._sliceChildren(r, a * t, a * e)
    );
  }
  _changeLengthAndBackfillNullBitmap(t) {
    if (this.typeId === l.Null)
      return this.clone(this.type, 0, t, 0);
    const { length: e, nullCount: i } = this, s = new Uint8Array((t + 63 & -64) >> 3).fill(255, 0, e >> 3);
    s[e >> 3] = (1 << e - (e & -8)) - 1, i > 0 && s.set(_s(this.offset, e, this.nullBitmap), 0);
    const r = this.buffers;
    return r[Nt.VALIDITY] = s, this.clone(this.type, 0, t, i + (t - e), r);
  }
  _sliceBuffers(t, e, i, s) {
    let r;
    const { buffers: o } = this;
    return (r = o[Nt.TYPE]) && (o[Nt.TYPE] = r.subarray(t, t + e)), (r = o[Nt.OFFSET]) && (o[Nt.OFFSET] = r.subarray(t, t + e + 1)) || // Otherwise if no offsets, slice the data buffer. Don't slice the data vector for Booleans, since the offset goes by bits not bytes
    (r = o[Nt.DATA]) && (o[Nt.DATA] = s === 6 ? r : r.subarray(i * t, i * (t + e))), o;
  }
  _sliceChildren(t, e, i) {
    return t.map((s) => s.slice(e, i));
  }
}
L.prototype.children = Object.freeze([]);
class ke extends M {
  visit(t) {
    return this.getVisitFn(t.type).call(this, t);
  }
  visitNull(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["length"]: s = 0 } = t;
    return new L(e, i, s, s);
  }
  visitBool(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length >> 3, ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitInt(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length, ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitFloat(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length, ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitUtf8(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.data), r = N(t.nullBitmap), o = Te(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, s, r]);
  }
  visitLargeUtf8(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.data), r = N(t.nullBitmap), o = rs(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, s, r]);
  }
  visitBinary(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.data), r = N(t.nullBitmap), o = Te(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, s, r]);
  }
  visitLargeBinary(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.data), r = N(t.nullBitmap), o = rs(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, s, r]);
  }
  visitFixedSizeBinary(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / It(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitDate(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / It(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitTimestamp(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / It(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitTime(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / It(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitDecimal(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / It(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitList(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["child"]: s } = t, r = N(t.nullBitmap), o = Te(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, void 0, r], [s]);
  }
  visitStruct(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["children"]: s = [] } = t, r = N(t.nullBitmap), { length: o = s.reduce((c, { length: u }) => Math.max(c, u), 0), nullCount: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, void 0, r], s);
  }
  visitUnion(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["children"]: s = [] } = t, r = R(e.ArrayType, t.typeIds), { ["length"]: o = r.length, ["nullCount"]: a = -1 } = t;
    if (f.isSparseUnion(e))
      return new L(e, i, o, a, [void 0, void 0, void 0, r], s);
    const c = Te(t.valueOffsets);
    return new L(e, i, o, a, [c, void 0, void 0, r], s);
  }
  visitDictionary(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.nullBitmap), r = R(e.indices.ArrayType, t.data), { ["dictionary"]: o = new A([new ke().visit({ type: e.dictionary })]) } = t, { ["length"]: a = r.length, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [void 0, r, s], [], o);
  }
  visitInterval(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length / It(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitDuration(t) {
    const { ["type"]: e, ["offset"]: i = 0 } = t, s = N(t.nullBitmap), r = R(e.ArrayType, t.data), { ["length"]: o = r.length, ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, r, s]);
  }
  visitFixedSizeList(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["child"]: s = new ke().visit({ type: e.valueType }) } = t, r = N(t.nullBitmap), { ["length"]: o = s.length / It(e), ["nullCount"]: a = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, o, a, [void 0, void 0, r], [s]);
  }
  visitMap(t) {
    const { ["type"]: e, ["offset"]: i = 0, ["child"]: s = new ke().visit({ type: e.childType }) } = t, r = N(t.nullBitmap), o = Te(t.valueOffsets), { ["length"]: a = o.length - 1, ["nullCount"]: c = t.nullBitmap ? -1 : 0 } = t;
    return new L(e, i, a, c, [o, void 0, r], [s]);
  }
}
const Dc = new ke();
function I(n) {
  return Dc.visit(n);
}
class vs {
  constructor(t = 0, e) {
    this.numChunks = t, this.getChunkIterator = e, this.chunkIndex = 0, this.chunkIterator = this.getChunkIterator(0);
  }
  next() {
    for (; this.chunkIndex < this.numChunks; ) {
      const t = this.chunkIterator.next();
      if (!t.done)
        return t;
      ++this.chunkIndex < this.numChunks && (this.chunkIterator = this.getChunkIterator(this.chunkIndex));
    }
    return { done: !0, value: null };
  }
  [Symbol.iterator]() {
    return this;
  }
}
function Oc(n) {
  return n.some((t) => t.nullable);
}
function lo(n) {
  return n.reduce((t, e) => t + e.nullCount, 0);
}
function uo(n) {
  return n.reduce((t, e, i) => (t[i + 1] = t[i] + e.length, t), new Uint32Array(n.length + 1));
}
function ho(n, t, e, i) {
  const s = [];
  for (let r = -1, o = n.length; ++r < o; ) {
    const a = n[r], c = t[r], { length: u } = a;
    if (c >= i)
      break;
    if (e >= c + u)
      continue;
    if (c >= e && c + u <= i) {
      s.push(a);
      continue;
    }
    const d = Math.max(0, e - c), h = Math.min(i - c, u);
    s.push(a.slice(d, h - d));
  }
  return s.length === 0 && s.push(n[0].slice(0, 0)), s;
}
function Yi(n, t, e, i) {
  let s = 0, r = 0, o = t.length - 1;
  do {
    if (s >= o - 1)
      return e < t[o] ? i(n, s, e - t[s]) : null;
    r = s + Math.trunc((o - s) * 0.5), e < t[r] ? o = r : s = r;
  } while (s < o);
}
function Wi(n, t) {
  return n.getValid(t);
}
function Mn(n) {
  function t(e, i, s) {
    return n(e[i], s);
  }
  return function(e) {
    const i = this.data;
    return Yi(i, this._offsets, e, t);
  };
}
function fo(n) {
  let t;
  function e(i, s, r) {
    return n(i[s], r, t);
  }
  return function(i, s) {
    const r = this.data;
    t = s;
    const o = Yi(r, this._offsets, i, e);
    return t = void 0, o;
  };
}
function po(n) {
  let t;
  function e(i, s, r) {
    let o = r, a = 0, c = 0;
    for (let u = s - 1, d = i.length; ++u < d; ) {
      const h = i[u];
      if (~(a = n(h, t, o)))
        return c + a;
      o = 0, c += h.length;
    }
    return -1;
  }
  return function(i, s) {
    t = i;
    const r = this.data, o = typeof s != "number" ? e(r, 0, 0) : Yi(r, this._offsets, s, e);
    return t = void 0, o;
  };
}
class y extends M {
}
function Fc(n, t) {
  return t === null && n.length > 0 ? 0 : -1;
}
function Mc(n, t) {
  const { nullBitmap: e } = n;
  if (!e || n.nullCount <= 0)
    return -1;
  let i = 0;
  for (const s of new $i(e, n.offset + (t || 0), n.length, e, co)) {
    if (!s)
      return i;
    ++i;
  }
  return -1;
}
function B(n, t, e) {
  if (t === void 0)
    return -1;
  if (t === null)
    switch (n.typeId) {
      // Unions don't have a nullBitmap of its own, so compare the `searchElement` to `get()`.
      case l.Union:
        break;
      // Dictionaries do have a nullBitmap, but their dictionary could also have null elements.
      case l.Dictionary:
        break;
      // All other types can iterate the null bitmap
      default:
        return Mc(n, e);
    }
  const i = et.getVisitFn(n), s = Ae(t);
  for (let r = (e || 0) - 1, o = n.length; ++r < o; )
    if (s(i(n, r)))
      return r;
  return -1;
}
function yo(n, t, e) {
  const i = et.getVisitFn(n), s = Ae(t);
  for (let r = (e || 0) - 1, o = n.length; ++r < o; )
    if (s(i(n, r)))
      return r;
  return -1;
}
y.prototype.visitNull = Fc;
y.prototype.visitBool = B;
y.prototype.visitInt = B;
y.prototype.visitInt8 = B;
y.prototype.visitInt16 = B;
y.prototype.visitInt32 = B;
y.prototype.visitInt64 = B;
y.prototype.visitUint8 = B;
y.prototype.visitUint16 = B;
y.prototype.visitUint32 = B;
y.prototype.visitUint64 = B;
y.prototype.visitFloat = B;
y.prototype.visitFloat16 = B;
y.prototype.visitFloat32 = B;
y.prototype.visitFloat64 = B;
y.prototype.visitUtf8 = B;
y.prototype.visitLargeUtf8 = B;
y.prototype.visitBinary = B;
y.prototype.visitLargeBinary = B;
y.prototype.visitFixedSizeBinary = B;
y.prototype.visitDate = B;
y.prototype.visitDateDay = B;
y.prototype.visitDateMillisecond = B;
y.prototype.visitTimestamp = B;
y.prototype.visitTimestampSecond = B;
y.prototype.visitTimestampMillisecond = B;
y.prototype.visitTimestampMicrosecond = B;
y.prototype.visitTimestampNanosecond = B;
y.prototype.visitTime = B;
y.prototype.visitTimeSecond = B;
y.prototype.visitTimeMillisecond = B;
y.prototype.visitTimeMicrosecond = B;
y.prototype.visitTimeNanosecond = B;
y.prototype.visitDecimal = B;
y.prototype.visitList = B;
y.prototype.visitStruct = B;
y.prototype.visitUnion = B;
y.prototype.visitDenseUnion = yo;
y.prototype.visitSparseUnion = yo;
y.prototype.visitDictionary = B;
y.prototype.visitInterval = B;
y.prototype.visitIntervalDayTime = B;
y.prototype.visitIntervalYearMonth = B;
y.prototype.visitIntervalMonthDayNano = B;
y.prototype.visitDuration = B;
y.prototype.visitDurationSecond = B;
y.prototype.visitDurationMillisecond = B;
y.prototype.visitDurationMicrosecond = B;
y.prototype.visitDurationNanosecond = B;
y.prototype.visitFixedSizeList = B;
y.prototype.visitMap = B;
const Nn = new y();
class g extends M {
}
function w(n) {
  const { type: t } = n;
  if (n.nullCount === 0 && n.stride === 1 && // Don't defer to native iterator for timestamps since Numbers are expected
  // (DataType.isTimestamp(type)) && type.unit === TimeUnit.MILLISECOND ||
  (f.isInt(t) && t.bitWidth !== 64 || f.isTime(t) && t.bitWidth !== 64 || f.isFloat(t) && t.precision !== W.HALF))
    return new vs(n.data.length, (i) => {
      const s = n.data[i];
      return s.values.subarray(0, s.length)[Symbol.iterator]();
    });
  let e = 0;
  return new vs(n.data.length, (i) => {
    const r = n.data[i].length, o = n.slice(e, e + r);
    return e += r, new Nc(o);
  });
}
class Nc {
  constructor(t) {
    this.vector = t, this.index = 0;
  }
  next() {
    return this.index < this.vector.length ? {
      value: this.vector.get(this.index++)
    } : { done: !0, value: null };
  }
  [Symbol.iterator]() {
    return this;
  }
}
g.prototype.visitNull = w;
g.prototype.visitBool = w;
g.prototype.visitInt = w;
g.prototype.visitInt8 = w;
g.prototype.visitInt16 = w;
g.prototype.visitInt32 = w;
g.prototype.visitInt64 = w;
g.prototype.visitUint8 = w;
g.prototype.visitUint16 = w;
g.prototype.visitUint32 = w;
g.prototype.visitUint64 = w;
g.prototype.visitFloat = w;
g.prototype.visitFloat16 = w;
g.prototype.visitFloat32 = w;
g.prototype.visitFloat64 = w;
g.prototype.visitUtf8 = w;
g.prototype.visitLargeUtf8 = w;
g.prototype.visitBinary = w;
g.prototype.visitLargeBinary = w;
g.prototype.visitFixedSizeBinary = w;
g.prototype.visitDate = w;
g.prototype.visitDateDay = w;
g.prototype.visitDateMillisecond = w;
g.prototype.visitTimestamp = w;
g.prototype.visitTimestampSecond = w;
g.prototype.visitTimestampMillisecond = w;
g.prototype.visitTimestampMicrosecond = w;
g.prototype.visitTimestampNanosecond = w;
g.prototype.visitTime = w;
g.prototype.visitTimeSecond = w;
g.prototype.visitTimeMillisecond = w;
g.prototype.visitTimeMicrosecond = w;
g.prototype.visitTimeNanosecond = w;
g.prototype.visitDecimal = w;
g.prototype.visitList = w;
g.prototype.visitStruct = w;
g.prototype.visitUnion = w;
g.prototype.visitDenseUnion = w;
g.prototype.visitSparseUnion = w;
g.prototype.visitDictionary = w;
g.prototype.visitInterval = w;
g.prototype.visitIntervalDayTime = w;
g.prototype.visitIntervalYearMonth = w;
g.prototype.visitIntervalMonthDayNano = w;
g.prototype.visitDuration = w;
g.prototype.visitDurationSecond = w;
g.prototype.visitDurationMillisecond = w;
g.prototype.visitDurationMicrosecond = w;
g.prototype.visitDurationNanosecond = w;
g.prototype.visitFixedSizeList = w;
g.prototype.visitMap = w;
const Hi = new g();
var go;
const mo = {}, bo = {};
class A {
  constructor(t) {
    var e, i, s;
    const r = t[0] instanceof A ? t.flatMap((a) => a.data) : t;
    if (r.length === 0 || r.some((a) => !(a instanceof L)))
      throw new TypeError("Vector constructor expects an Array of Data instances.");
    const o = (e = r[0]) === null || e === void 0 ? void 0 : e.type;
    switch (r.length) {
      case 0:
        this._offsets = [0];
        break;
      case 1: {
        const { get: a, set: c, indexOf: u } = mo[o.typeId], d = r[0];
        this.isValid = (h) => Wi(d, h), this.get = (h) => a(d, h), this.set = (h, T) => c(d, h, T), this.indexOf = (h) => u(d, h), this._offsets = [0, d.length];
        break;
      }
      default:
        Object.setPrototypeOf(this, bo[o.typeId]), this._offsets = uo(r);
        break;
    }
    this.data = r, this.type = o, this.stride = It(o), this.numChildren = (s = (i = o.children) === null || i === void 0 ? void 0 : i.length) !== null && s !== void 0 ? s : 0, this.length = this._offsets.at(-1);
  }
  /**
   * The aggregate size (in bytes) of this Vector's buffers and/or child Vectors.
   */
  get byteLength() {
    return this.data.reduce((t, e) => t + e.byteLength, 0);
  }
  /**
   * Whether this Vector's elements can contain null values.
   */
  get nullable() {
    return Oc(this.data);
  }
  /**
   * The number of null elements in this Vector.
   */
  get nullCount() {
    return lo(this.data);
  }
  /**
   * The Array or TypedArray constructor used for the JS representation
   *  of the element's values in {@link Vector.prototype.toArray `toArray()`}.
   */
  get ArrayType() {
    return this.type.ArrayType;
  }
  /**
   * The name that should be printed when the Vector is logged in a message.
   */
  get [Symbol.toStringTag]() {
    return `${this.VectorName}<${this.type[Symbol.toStringTag]}>`;
  }
  /**
   * The name of this Vector.
   */
  get VectorName() {
    return `${l[this.type.typeId]}Vector`;
  }
  /**
   * Check whether an element is null.
   * @param index The index at which to read the validity bitmap.
   */
  // @ts-ignore
  isValid(t) {
    return !1;
  }
  /**
   * Get an element value by position.
   * @param index The index of the element to read.
   */
  // @ts-ignore
  get(t) {
    return null;
  }
  /**
   * Get an element value by position.
   * @param index The index of the element to read. A negative index will count back from the last element.
   */
  at(t) {
    return this.get(ji(t, this.length));
  }
  /**
   * Set an element value by position.
   * @param index The index of the element to write.
   * @param value The value to set.
   */
  // @ts-ignore
  set(t, e) {
  }
  /**
   * Retrieve the index of the first occurrence of a value in an Vector.
   * @param element The value to locate in the Vector.
   * @param offset The index at which to begin the search. If offset is omitted, the search starts at index 0.
   */
  // @ts-ignore
  indexOf(t, e) {
    return -1;
  }
  includes(t, e) {
    return this.indexOf(t, e) > -1;
  }
  /**
   * Iterator for the Vector's elements.
   */
  [Symbol.iterator]() {
    return Hi.visit(this);
  }
  /**
   * Combines two or more Vectors of the same type.
   * @param others Additional Vectors to add to the end of this Vector.
   */
  concat(...t) {
    return new A(this.data.concat(t.flatMap((e) => e.data).flat(Number.POSITIVE_INFINITY)));
  }
  /**
   * Return a zero-copy sub-section of this Vector.
   * @param start The beginning of the specified portion of the Vector.
   * @param end The end of the specified portion of the Vector. This is exclusive of the element at the index 'end'.
   */
  slice(t, e) {
    return new A(ao(this, t, e, ({ data: i, _offsets: s }, r, o) => ho(i, s, r, o)));
  }
  toJSON() {
    return [...this];
  }
  /**
   * Return a JavaScript Array or TypedArray of the Vector's elements.
   *
   * @note If this Vector contains a single Data chunk and the Vector's type is a
   *  primitive numeric type corresponding to one of the JavaScript TypedArrays, this
   *  method returns a zero-copy slice of the underlying TypedArray values. If there's
   *  more than one chunk, the resulting TypedArray will be a copy of the data from each
   *  chunk's underlying TypedArray values.
   *
   * @returns An Array or TypedArray of the Vector's elements, based on the Vector's DataType.
   */
  toArray() {
    const { type: t, data: e, length: i, stride: s, ArrayType: r } = this;
    switch (t.typeId) {
      case l.Int:
      case l.Float:
      case l.Decimal:
      case l.Time:
      case l.Timestamp:
        switch (e.length) {
          case 0:
            return new r();
          case 1:
            return e[0].values.subarray(0, i * s);
          default:
            return e.reduce((o, { values: a, length: c }) => (o.array.set(a.subarray(0, c * s), o.offset), o.offset += c * s, o), { array: new r(i * s), offset: 0 }).array;
        }
    }
    return [...this];
  }
  /**
   * Returns a string representation of the Vector.
   *
   * @returns A string representation of the Vector.
   */
  toString() {
    return `[${[...this].join(",")}]`;
  }
  /**
   * Returns a child Vector by name, or null if this Vector has no child with the given name.
   * @param name The name of the child to retrieve.
   */
  getChild(t) {
    var e;
    return this.getChildAt((e = this.type.children) === null || e === void 0 ? void 0 : e.findIndex((i) => i.name === t));
  }
  /**
   * Returns a child Vector by index, or null if this Vector has no child at the supplied index.
   * @param index The index of the child to retrieve.
   */
  getChildAt(t) {
    return t > -1 && t < this.numChildren ? new A(this.data.map(({ children: e }) => e[t])) : null;
  }
  get isMemoized() {
    return f.isDictionary(this.type) ? this.data[0].dictionary.isMemoized : !1;
  }
  /**
   * Adds memoization to the Vector's {@link get} method. For dictionary
   * vectors, this method return a vector that memoizes only the dictionary
   * values.
   *
   * Memoization is very useful when decoding a value is expensive such as
   * Utf8. The memoization creates a cache of the size of the Vector and
   * therefore increases memory usage.
   *
   * @returns A new vector that memoizes calls to {@link get}.
   */
  memoize() {
    if (f.isDictionary(this.type)) {
      const t = new Tn(this.data[0].dictionary), e = this.data.map((i) => {
        const s = i.clone();
        return s.dictionary = t, s;
      });
      return new A(e);
    }
    return new Tn(this);
  }
  /**
   * Returns a vector without memoization of the {@link get} method. If this
   * vector is not memoized, this method returns this vector.
   *
   * @returns A new vector without memoization.
   */
  unmemoize() {
    if (f.isDictionary(this.type) && this.isMemoized) {
      const t = this.data[0].dictionary.unmemoize(), e = this.data.map((i) => {
        const s = i.clone();
        return s.dictionary = t, s;
      });
      return new A(e);
    }
    return this;
  }
}
go = Symbol.toStringTag;
A[go] = ((n) => {
  n.type = f.prototype, n.data = [], n.length = 0, n.stride = 1, n.numChildren = 0, n._offsets = new Uint32Array([0]), n[Symbol.isConcatSpreadable] = !0;
  const t = Object.keys(l).map((e) => l[e]).filter((e) => typeof e == "number" && e !== l.NONE);
  for (const e of t) {
    const i = et.getVisitFnByTypeId(e), s = ht.getVisitFnByTypeId(e), r = Nn.getVisitFnByTypeId(e);
    mo[e] = { get: i, set: s, indexOf: r }, bo[e] = Object.create(n, {
      isValid: { value: Mn(Wi) },
      get: { value: Mn(et.getVisitFnByTypeId(e)) },
      set: { value: fo(ht.getVisitFnByTypeId(e)) },
      indexOf: { value: po(Nn.getVisitFnByTypeId(e)) }
    });
  }
  return "Vector";
})(A.prototype);
class Tn extends A {
  constructor(t) {
    super(t.data);
    const e = this.get, i = this.set, s = this.slice, r = new Array(this.length);
    Object.defineProperty(this, "get", {
      value(o) {
        const a = r[o];
        if (a !== void 0)
          return a;
        const c = e.call(this, o);
        return r[o] = c, c;
      }
    }), Object.defineProperty(this, "set", {
      value(o, a) {
        i.call(this, o, a), r[o] = a;
      }
    }), Object.defineProperty(this, "slice", {
      value: (o, a) => new Tn(s.call(this, o, a))
    }), Object.defineProperty(this, "isMemoized", { value: !0 }), Object.defineProperty(this, "unmemoize", {
      value: () => new A(this.data)
    }), Object.defineProperty(this, "memoize", {
      value: () => this
    });
  }
}
function _o(n) {
  if (n) {
    if (n instanceof L)
      return new A([n]);
    if (n instanceof A)
      return new A(n.data);
    if (n.type instanceof f)
      return new A([I(n)]);
    if (Array.isArray(n))
      return new A(n.flatMap((t) => Tc(t)));
    if (ArrayBuffer.isView(n)) {
      n instanceof DataView && (n = new Uint8Array(n.buffer));
      const t = { offset: 0, length: n.length, nullCount: -1, data: n };
      if (n instanceof Int8Array)
        return new A([I(Object.assign(Object.assign({}, t), { type: new yr() }))]);
      if (n instanceof Int16Array)
        return new A([I(Object.assign(Object.assign({}, t), { type: new gr() }))]);
      if (n instanceof Int32Array)
        return new A([I(Object.assign(Object.assign({}, t), { type: new Yt() }))]);
      if (n instanceof BigInt64Array)
        return new A([I(Object.assign(Object.assign({}, t), { type: new Ii() }))]);
      if (n instanceof Uint8Array || n instanceof Uint8ClampedArray)
        return new A([I(Object.assign(Object.assign({}, t), { type: new mr() }))]);
      if (n instanceof Uint16Array)
        return new A([I(Object.assign(Object.assign({}, t), { type: new br() }))]);
      if (n instanceof Uint32Array)
        return new A([I(Object.assign(Object.assign({}, t), { type: new _r() }))]);
      if (n instanceof BigUint64Array)
        return new A([I(Object.assign(Object.assign({}, t), { type: new vr() }))]);
      if (n instanceof Float32Array)
        return new A([I(Object.assign(Object.assign({}, t), { type: new wr() }))]);
      if (n instanceof Float64Array)
        return new A([I(Object.assign(Object.assign({}, t), { type: new jn() }))]);
      throw new Error("Unrecognized input");
    }
  }
  throw new Error("Unrecognized input");
}
function Tc(n) {
  return n instanceof L ? [n] : n instanceof A ? n.data : _o(n).data;
}
function Lc(n) {
  if (!n || n.length <= 0)
    return function(s) {
      return !0;
    };
  let t = "";
  const e = n.filter((i) => i === i);
  return e.length > 0 && (t = `
    switch (x) {${e.map((i) => `
        case ${Uc(i)}:`).join("")}
            return false;
    }`), n.length !== e.length && (t = `if (x !== x) return false;
${t}`), new Function("x", `${t}
return true;`);
}
function Uc(n) {
  return typeof n != "bigint" ? te(n) : `${te(n)}n`;
}
function ei(n, t) {
  const e = Math.ceil(n) * t - 1;
  return (e - e % 64 + 64 || 64) / t;
}
function ws(n, t = 0) {
  return n.length >= t ? n.subarray(0, t) : ai(new n.constructor(t), n, 0);
}
class Qe {
  constructor(t, e = 0, i = 1) {
    this.length = Math.ceil(e / i), this.buffer = new t(this.length), this.stride = i, this.BYTES_PER_ELEMENT = t.BYTES_PER_ELEMENT, this.ArrayType = t;
  }
  get byteLength() {
    return Math.ceil(this.length * this.stride) * this.BYTES_PER_ELEMENT;
  }
  get reservedLength() {
    return this.buffer.length / this.stride;
  }
  get reservedByteLength() {
    return this.buffer.byteLength;
  }
  // @ts-ignore
  set(t, e) {
    return this;
  }
  append(t) {
    return this.set(this.length, t);
  }
  reserve(t) {
    if (t > 0) {
      this.length += t;
      const e = this.stride, i = this.length * e, s = this.buffer.length;
      i >= s && this._resize(s === 0 ? ei(i * 1, this.BYTES_PER_ELEMENT) : ei(i * 2, this.BYTES_PER_ELEMENT));
    }
    return this;
  }
  flush(t = this.length) {
    t = ei(t * this.stride, this.BYTES_PER_ELEMENT);
    const e = ws(this.buffer, t);
    return this.clear(), e;
  }
  clear() {
    return this.length = 0, this.buffer = new this.ArrayType(), this;
  }
  _resize(t) {
    return this.buffer = ws(this.buffer, t);
  }
}
class Xe extends Qe {
  last() {
    return this.get(this.length - 1);
  }
  get(t) {
    return this.buffer[t];
  }
  set(t, e) {
    return this.reserve(t - this.length + 1), this.buffer[t * this.stride] = e, this;
  }
}
class vo extends Xe {
  constructor() {
    super(Uint8Array, 0, 1 / 8), this.numValid = 0;
  }
  get numInvalid() {
    return this.length - this.numValid;
  }
  get(t) {
    return this.buffer[t >> 3] >> t % 8 & 1;
  }
  set(t, e) {
    const { buffer: i } = this.reserve(t - this.length + 1), s = t >> 3, r = t % 8, o = i[s] >> r & 1;
    return e ? o === 0 && (i[s] |= 1 << r, ++this.numValid) : o === 1 && (i[s] &= ~(1 << r), --this.numValid), this;
  }
  clear() {
    return this.numValid = 0, super.clear();
  }
}
class wo extends Xe {
  constructor(t) {
    super(t.OffsetArrayType, 1, 1);
  }
  append(t) {
    return this.set(this.length - 1, t);
  }
  set(t, e) {
    const i = this.length - 1, s = this.reserve(t - i + 1).buffer;
    return i < t++ && i >= 0 && s.fill(s[i], i, t), s[t] = s[t - 1] + e, this;
  }
  flush(t = this.length - 1) {
    return t > this.length && this.set(t - 1, this.BYTES_PER_ELEMENT > 4 ? BigInt(0) : 0), super.flush(t + 1);
  }
}
let nt = class {
  /** @nocollapse */
  // @ts-ignore
  static throughNode(t) {
    throw new Error('"throughNode" not available in this environment');
  }
  /** @nocollapse */
  // @ts-ignore
  static throughDOM(t) {
    throw new Error('"throughDOM" not available in this environment');
  }
  /**
   * Construct a builder with the given Arrow DataType with optional null values,
   * which will be interpreted as "null" when set or appended to the `Builder`.
   * @param {{ type: T, nullValues?: any[] }} options A `BuilderOptions` object used to create this `Builder`.
   */
  constructor({ type: t, nullValues: e }) {
    this.length = 0, this.finished = !1, this.type = t, this.children = [], this.nullValues = e, this.stride = It(t), this._nulls = new vo(), e && e.length > 0 && (this._isValid = Lc(e));
  }
  /**
   * Flush the `Builder` and return a `Vector<T>`.
   * @returns {Vector<T>} A `Vector<T>` of the flushed values.
   */
  toVector() {
    return new A([this.flush()]);
  }
  get ArrayType() {
    return this.type.ArrayType;
  }
  get nullCount() {
    return this._nulls.numInvalid;
  }
  get numChildren() {
    return this.children.length;
  }
  /**
   * @returns The aggregate length (in bytes) of the values that have been written.
   */
  get byteLength() {
    let t = 0;
    const { _offsets: e, _values: i, _nulls: s, _typeIds: r, children: o } = this;
    return e && (t += e.byteLength), i && (t += i.byteLength), s && (t += s.byteLength), r && (t += r.byteLength), o.reduce((a, c) => a + c.byteLength, t);
  }
  /**
   * @returns The aggregate number of rows that have been reserved to write new values.
   */
  get reservedLength() {
    return this._nulls.reservedLength;
  }
  /**
   * @returns The aggregate length (in bytes) that has been reserved to write new values.
   */
  get reservedByteLength() {
    let t = 0;
    return this._offsets && (t += this._offsets.reservedByteLength), this._values && (t += this._values.reservedByteLength), this._nulls && (t += this._nulls.reservedByteLength), this._typeIds && (t += this._typeIds.reservedByteLength), this.children.reduce((e, i) => e + i.reservedByteLength, t);
  }
  get valueOffsets() {
    return this._offsets ? this._offsets.buffer : null;
  }
  get values() {
    return this._values ? this._values.buffer : null;
  }
  get nullBitmap() {
    return this._nulls ? this._nulls.buffer : null;
  }
  get typeIds() {
    return this._typeIds ? this._typeIds.buffer : null;
  }
  /**
   * Appends a value (or null) to this `Builder`.
   * This is equivalent to `builder.set(builder.length, value)`.
   * @param {T['TValue'] | TNull } value The value to append.
   */
  append(t) {
    return this.set(this.length, t);
  }
  /**
   * Validates whether a value is valid (true), or null (false)
   * @param {T['TValue'] | TNull } value The value to compare against null the value representations
   */
  isValid(t) {
    return this._isValid(t);
  }
  /**
   * Write a value (or null-value sentinel) at the supplied index.
   * If the value matches one of the null-value representations, a 1-bit is
   * written to the null `BitmapBufferBuilder`. Otherwise, a 0 is written to
   * the null `BitmapBufferBuilder`, and the value is passed to
   * `Builder.prototype.setValue()`.
   * @param {number} index The index of the value to write.
   * @param {T['TValue'] | TNull } value The value to write at the supplied index.
   * @returns {this} The updated `Builder` instance.
   */
  set(t, e) {
    return this.setValid(t, this.isValid(e)) && this.setValue(t, e), this;
  }
  /**
   * Write a value to the underlying buffers at the supplied index, bypassing
   * the null-value check. This is a low-level method that
   * @param {number} index
   * @param {T['TValue'] | TNull } value
   */
  setValue(t, e) {
    this._setValue(this, t, e);
  }
  setValid(t, e) {
    return this.length = this._nulls.set(t, +e).length, e;
  }
  // @ts-ignore
  addChild(t, e = `${this.numChildren}`) {
    throw new Error(`Cannot append children to non-nested type "${this.type}"`);
  }
  /**
   * Retrieve the child `Builder` at the supplied `index`, or null if no child
   * exists at that index.
   * @param {number} index The index of the child `Builder` to retrieve.
   * @returns {Builder | null} The child Builder at the supplied index or null.
   */
  getChildAt(t) {
    return this.children[t] || null;
  }
  /**
   * Commit all the values that have been written to their underlying
   * ArrayBuffers, including any child Builders if applicable, and reset
   * the internal `Builder` state.
   * @returns A `Data<T>` of the buffers and children representing the values written.
   */
  flush() {
    let t, e, i, s;
    const { type: r, length: o, nullCount: a, _typeIds: c, _offsets: u, _values: d, _nulls: h } = this;
    (e = c?.flush(o)) ? s = u?.flush(o) : (s = u?.flush(o)) ? t = d?.flush(u.last()) : t = d?.flush(o), a > 0 && (i = h?.flush(o));
    const T = this.children.map((O) => O.flush());
    return this.clear(), I({
      type: r,
      length: o,
      nullCount: a,
      children: T,
      child: T[0],
      data: t,
      typeIds: e,
      nullBitmap: i,
      valueOffsets: s
    });
  }
  /**
   * Finalize this `Builder`, and child builders if applicable.
   * @returns {this} The finalized `Builder` instance.
   */
  finish() {
    this.finished = !0;
    for (const t of this.children)
      t.finish();
    return this;
  }
  /**
   * Clear this Builder's internal state, including child Builders if applicable, and reset the length to 0.
   * @returns {this} The cleared `Builder` instance.
   */
  clear() {
    var t, e, i, s;
    this.length = 0, (t = this._nulls) === null || t === void 0 || t.clear(), (e = this._values) === null || e === void 0 || e.clear(), (i = this._offsets) === null || i === void 0 || i.clear(), (s = this._typeIds) === null || s === void 0 || s.clear();
    for (const r of this.children)
      r.clear();
    return this;
  }
};
nt.prototype.length = 1;
nt.prototype.stride = 1;
nt.prototype.children = null;
nt.prototype.finished = !1;
nt.prototype.nullValues = null;
nt.prototype._isValid = () => !0;
class Vt extends nt {
  constructor(t) {
    super(t), this._values = new Xe(this.ArrayType, 0, this.stride);
  }
  setValue(t, e) {
    const i = this._values;
    return i.reserve(t - i.length + 1), super.setValue(t, e);
  }
}
class De extends nt {
  constructor(t) {
    super(t), this._pendingLength = 0, this._offsets = new wo(t.type);
  }
  setValue(t, e) {
    const i = this._pending || (this._pending = /* @__PURE__ */ new Map()), s = i.get(t);
    s && (this._pendingLength -= s.length), this._pendingLength += e instanceof $n ? e[qt].length : e.length, i.set(t, e);
  }
  setValid(t, e) {
    return super.setValid(t, e) ? !0 : ((this._pending || (this._pending = /* @__PURE__ */ new Map())).set(t, void 0), !1);
  }
  clear() {
    return this._pendingLength = 0, this._pending = void 0, super.clear();
  }
  flush() {
    return this._flush(), super.flush();
  }
  finish() {
    return this._flush(), super.finish();
  }
  _flush() {
    const t = this._pending, e = this._pendingLength;
    return this._pendingLength = 0, this._pending = void 0, t && t.size > 0 && this._flushPending(t, e), this;
  }
}
class yi {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  /**
   * Index to the start of the RecordBlock (note this is past the Message header)
   */
  offset() {
    return this.bb.readInt64(this.bb_pos);
  }
  /**
   * Length of the metadata
   */
  metaDataLength() {
    return this.bb.readInt32(this.bb_pos + 8);
  }
  /**
   * Length of the data (this is aligned so there can be a gap between this and
   * the metadata).
   */
  bodyLength() {
    return this.bb.readInt64(this.bb_pos + 16);
  }
  static sizeOf() {
    return 24;
  }
  static createBlock(t, e, i, s) {
    return t.prep(8, 24), t.writeInt64(BigInt(s ?? 0)), t.pad(4), t.writeInt32(i), t.writeInt64(BigInt(e ?? 0)), t.offset();
  }
}
class it {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsFooter(t, e) {
    return (e || new it()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsFooter(t, e) {
    return t.setPosition(t.position() + E), (e || new it()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  version() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : $.V1;
  }
  schema(t) {
    const e = this.bb.__offset(this.bb_pos, 6);
    return e ? (t || new vt()).__init(this.bb.__indirect(this.bb_pos + e), this.bb) : null;
  }
  dictionaries(t, e) {
    const i = this.bb.__offset(this.bb_pos, 8);
    return i ? (e || new yi()).__init(this.bb.__vector(this.bb_pos + i) + t * 24, this.bb) : null;
  }
  dictionariesLength() {
    const t = this.bb.__offset(this.bb_pos, 8);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  recordBatches(t, e) {
    const i = this.bb.__offset(this.bb_pos, 10);
    return i ? (e || new yi()).__init(this.bb.__vector(this.bb_pos + i) + t * 24, this.bb) : null;
  }
  recordBatchesLength() {
    const t = this.bb.__offset(this.bb_pos, 10);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  /**
   * User-defined metadata
   */
  customMetadata(t, e) {
    const i = this.bb.__offset(this.bb_pos, 12);
    return i ? (e || new Y()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  customMetadataLength() {
    const t = this.bb.__offset(this.bb_pos, 12);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  static startFooter(t) {
    t.startObject(5);
  }
  static addVersion(t, e) {
    t.addFieldInt16(0, e, $.V1);
  }
  static addSchema(t, e) {
    t.addFieldOffset(1, e, 0);
  }
  static addDictionaries(t, e) {
    t.addFieldOffset(2, e, 0);
  }
  static startDictionariesVector(t, e) {
    t.startVector(24, e, 8);
  }
  static addRecordBatches(t, e) {
    t.addFieldOffset(3, e, 0);
  }
  static startRecordBatchesVector(t, e) {
    t.startVector(24, e, 8);
  }
  static addCustomMetadata(t, e) {
    t.addFieldOffset(4, e, 0);
  }
  static createCustomMetadataVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startCustomMetadataVector(t, e) {
    t.startVector(4, e, 4);
  }
  static endFooter(t) {
    return t.endObject();
  }
  static finishFooterBuffer(t, e) {
    t.finish(e);
  }
  static finishSizePrefixedFooterBuffer(t, e) {
    t.finish(e, void 0, !0);
  }
}
class C {
  constructor(t = [], e, i, s = $.V5) {
    this.fields = t || [], this.metadata = e || /* @__PURE__ */ new Map(), i || (i = gi(this.fields)), this.dictionaries = i, this.metadataVersion = s;
  }
  get [Symbol.toStringTag]() {
    return "Schema";
  }
  get names() {
    return this.fields.map((t) => t.name);
  }
  toString() {
    return `Schema<{ ${this.fields.map((t, e) => `${e}: ${t}`).join(", ")} }>`;
  }
  /**
   * Construct a new Schema containing only specified fields.
   *
   * @param fieldNames Names of fields to keep.
   * @returns A new Schema of fields matching the specified names.
   */
  select(t) {
    const e = new Set(t), i = this.fields.filter((s) => e.has(s.name));
    return new C(i, this.metadata);
  }
  /**
   * Construct a new Schema containing only fields at the specified indices.
   *
   * @param fieldIndices Indices of fields to keep.
   * @returns A new Schema of fields at the specified indices.
   */
  selectAt(t) {
    const e = t.map((i) => this.fields[i]).filter(Boolean);
    return new C(e, this.metadata);
  }
  assign(...t) {
    const e = t[0] instanceof C ? t[0] : Array.isArray(t[0]) ? new C(t[0]) : new C(t), i = [...this.fields], s = cn(cn(/* @__PURE__ */ new Map(), this.metadata), e.metadata), r = e.fields.filter((a) => {
      const c = i.findIndex((u) => u.name === a.name);
      return ~c ? (i[c] = a.clone({
        metadata: cn(cn(/* @__PURE__ */ new Map(), i[c].metadata), a.metadata)
      })) && !1 : !0;
    }), o = gi(r, /* @__PURE__ */ new Map());
    return new C([...i, ...r], s, new Map([...this.dictionaries, ...o]));
  }
}
C.prototype.fields = null;
C.prototype.metadata = null;
C.prototype.dictionaries = null;
class U {
  /** @nocollapse */
  static new(...t) {
    let [e, i, s, r] = t;
    return t[0] && typeof t[0] == "object" && ({ name: e } = t[0], i === void 0 && (i = t[0].type), s === void 0 && (s = t[0].nullable), r === void 0 && (r = t[0].metadata)), new U(`${e}`, i, s, r);
  }
  constructor(t, e, i = !1, s) {
    this.name = t, this.type = e, this.nullable = i, this.metadata = s || /* @__PURE__ */ new Map();
  }
  get typeId() {
    return this.type.typeId;
  }
  get [Symbol.toStringTag]() {
    return "Field";
  }
  toString() {
    return `${this.name}: ${this.type}`;
  }
  clone(...t) {
    let [e, i, s, r] = t;
    return !t[0] || typeof t[0] != "object" ? [e = this.name, i = this.type, s = this.nullable, r = this.metadata] = t : { name: e = this.name, type: i = this.type, nullable: s = this.nullable, metadata: r = this.metadata } = t[0], U.new(e, i, s, r);
  }
}
U.prototype.type = null;
U.prototype.name = null;
U.prototype.nullable = null;
U.prototype.metadata = null;
function cn(n, t) {
  return new Map([...n || /* @__PURE__ */ new Map(), ...t || /* @__PURE__ */ new Map()]);
}
function gi(n, t = /* @__PURE__ */ new Map()) {
  for (let e = -1, i = n.length; ++e < i; ) {
    const r = n[e].type;
    if (f.isDictionary(r)) {
      if (!t.has(r.id))
        t.set(r.id, r.dictionary);
      else if (t.get(r.id) !== r.dictionary)
        throw new Error("Cannot create Schema containing two different dictionaries with the same Id");
    }
    r.children && r.children.length > 0 && gi(r.children, t);
  }
  return t;
}
var xc = Ps, Cc = Qt;
class Ji {
  /** @nocollapse */
  static decode(t) {
    t = new Cc(N(t));
    const e = it.getRootAsFooter(t), i = C.decode(e.schema(), /* @__PURE__ */ new Map(), e.version());
    return new Ec(i, e);
  }
  /** @nocollapse */
  static encode(t) {
    const e = new xc(), i = C.encode(e, t.schema);
    it.startRecordBatchesVector(e, t.numRecordBatches);
    for (const o of [...t.recordBatches()].slice().reverse())
      Ie.encode(e, o);
    const s = e.endVector();
    it.startDictionariesVector(e, t.numDictionaries);
    for (const o of [...t.dictionaryBatches()].slice().reverse())
      Ie.encode(e, o);
    const r = e.endVector();
    return it.startFooter(e), it.addSchema(e, i), it.addVersion(e, $.V5), it.addRecordBatches(e, s), it.addDictionaries(e, r), it.finishFooterBuffer(e, it.endFooter(e)), e.asUint8Array();
  }
  get numRecordBatches() {
    return this._recordBatches.length;
  }
  get numDictionaries() {
    return this._dictionaryBatches.length;
  }
  constructor(t, e = $.V5, i, s) {
    this.schema = t, this.version = e, i && (this._recordBatches = i), s && (this._dictionaryBatches = s);
  }
  *recordBatches() {
    for (let t, e = -1, i = this.numRecordBatches; ++e < i; )
      (t = this.getRecordBatch(e)) && (yield t);
  }
  *dictionaryBatches() {
    for (let t, e = -1, i = this.numDictionaries; ++e < i; )
      (t = this.getDictionaryBatch(e)) && (yield t);
  }
  getRecordBatch(t) {
    return t >= 0 && t < this.numRecordBatches && this._recordBatches[t] || null;
  }
  getDictionaryBatch(t) {
    return t >= 0 && t < this.numDictionaries && this._dictionaryBatches[t] || null;
  }
}
class Ec extends Ji {
  get numRecordBatches() {
    return this._footer.recordBatchesLength();
  }
  get numDictionaries() {
    return this._footer.dictionariesLength();
  }
  constructor(t, e) {
    super(t, e.version()), this._footer = e;
  }
  getRecordBatch(t) {
    if (t >= 0 && t < this.numRecordBatches) {
      const e = this._footer.recordBatches(t);
      if (e)
        return Ie.decode(e);
    }
    return null;
  }
  getDictionaryBatch(t) {
    if (t >= 0 && t < this.numDictionaries) {
      const e = this._footer.dictionaries(t);
      if (e)
        return Ie.decode(e);
    }
    return null;
  }
}
class Ie {
  /** @nocollapse */
  static decode(t) {
    return new Ie(t.metaDataLength(), t.bodyLength(), t.offset());
  }
  /** @nocollapse */
  static encode(t, e) {
    const { metaDataLength: i } = e, s = BigInt(e.offset), r = BigInt(e.bodyLength);
    return yi.createBlock(t, s, i, r);
  }
  constructor(t, e, i) {
    this.metaDataLength = t, this.offset = k(i), this.bodyLength = k(e);
  }
}
let Pt = class bt {
  constructor() {
    this.bb = null, this.bb_pos = 0;
  }
  __init(t, e) {
    return this.bb_pos = t, this.bb = e, this;
  }
  static getRootAsMessage(t, e) {
    return (e || new bt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  static getSizePrefixedRootAsMessage(t, e) {
    return t.setPosition(t.position() + E), (e || new bt()).__init(t.readInt32(t.position()) + t.position(), t);
  }
  version() {
    const t = this.bb.__offset(this.bb_pos, 4);
    return t ? this.bb.readInt16(this.bb_pos + t) : $.V1;
  }
  headerType() {
    const t = this.bb.__offset(this.bb_pos, 6);
    return t ? this.bb.readUint8(this.bb_pos + t) : x.NONE;
  }
  header(t) {
    const e = this.bb.__offset(this.bb_pos, 8);
    return e ? this.bb.__union(t, this.bb_pos + e) : null;
  }
  bodyLength() {
    const t = this.bb.__offset(this.bb_pos, 10);
    return t ? this.bb.readInt64(this.bb_pos + t) : BigInt("0");
  }
  customMetadata(t, e) {
    const i = this.bb.__offset(this.bb_pos, 12);
    return i ? (e || new Y()).__init(this.bb.__indirect(this.bb.__vector(this.bb_pos + i) + t * 4), this.bb) : null;
  }
  customMetadataLength() {
    const t = this.bb.__offset(this.bb_pos, 12);
    return t ? this.bb.__vector_len(this.bb_pos + t) : 0;
  }
  static startMessage(t) {
    t.startObject(5);
  }
  static addVersion(t, e) {
    t.addFieldInt16(0, e, $.V1);
  }
  static addHeaderType(t, e) {
    t.addFieldInt8(1, e, x.NONE);
  }
  static addHeader(t, e) {
    t.addFieldOffset(2, e, 0);
  }
  static addBodyLength(t, e) {
    t.addFieldInt64(3, e, BigInt("0"));
  }
  static addCustomMetadata(t, e) {
    t.addFieldOffset(4, e, 0);
  }
  static createCustomMetadataVector(t, e) {
    t.startVector(4, e.length, 4);
    for (let i = e.length - 1; i >= 0; i--)
      t.addOffset(e[i]);
    return t.endVector();
  }
  static startCustomMetadataVector(t, e) {
    t.startVector(4, e, 4);
  }
  static endMessage(t) {
    return t.endObject();
  }
  static finishMessageBuffer(t, e) {
    t.finish(e);
  }
  static finishSizePrefixedMessageBuffer(t, e) {
    t.finish(e, void 0, !0);
  }
  static createMessage(t, e, i, s, r, o) {
    return bt.startMessage(t), bt.addVersion(t, e), bt.addHeaderType(t, i), bt.addHeader(t, s), bt.addBodyLength(t, r), bt.addCustomMetadata(t, o), bt.endMessage(t);
  }
};
class Vc extends M {
  visit(t, e) {
    return t == null || e == null ? void 0 : super.visit(t, e);
  }
  visitNull(t, e) {
    return fs.startNull(e), fs.endNull(e);
  }
  visitInt(t, e) {
    return st.startInt(e), st.addBitWidth(e, t.bitWidth), st.addIsSigned(e, t.isSigned), st.endInt(e);
  }
  visitFloat(t, e) {
    return Bt.startFloatingPoint(e), Bt.addPrecision(e, t.precision), Bt.endFloatingPoint(e);
  }
  visitBinary(t, e) {
    return cs.startBinary(e), cs.endBinary(e);
  }
  visitLargeBinary(t, e) {
    return us.startLargeBinary(e), us.endLargeBinary(e);
  }
  visitBool(t, e) {
    return ls.startBool(e), ls.endBool(e);
  }
  visitUtf8(t, e) {
    return ps.startUtf8(e), ps.endUtf8(e);
  }
  visitLargeUtf8(t, e) {
    return ds.startLargeUtf8(e), ds.endLargeUtf8(e);
  }
  visitDecimal(t, e) {
    return re.startDecimal(e), re.addScale(e, t.scale), re.addPrecision(e, t.precision), re.addBitWidth(e, t.bitWidth), re.endDecimal(e);
  }
  visitDate(t, e) {
    return un.startDate(e), un.addUnit(e, t.unit), un.endDate(e);
  }
  visitTime(t, e) {
    return lt.startTime(e), lt.addUnit(e, t.unit), lt.addBitWidth(e, t.bitWidth), lt.endTime(e);
  }
  visitTimestamp(t, e) {
    const i = t.timezone && e.createString(t.timezone) || void 0;
    return ut.startTimestamp(e), ut.addUnit(e, t.unit), i !== void 0 && ut.addTimezone(e, i), ut.endTimestamp(e);
  }
  visitInterval(t, e) {
    return At.startInterval(e), At.addUnit(e, t.unit), At.endInterval(e);
  }
  visitDuration(t, e) {
    return dn.startDuration(e), dn.addUnit(e, t.unit), dn.endDuration(e);
  }
  visitList(t, e) {
    return hs.startList(e), hs.endList(e);
  }
  visitStruct(t, e) {
    return Zt.startStruct_(e), Zt.endStruct_(e);
  }
  visitUnion(t, e) {
    Q.startTypeIdsVector(e, t.typeIds.length);
    const i = Q.createTypeIdsVector(e, t.typeIds);
    return Q.startUnion(e), Q.addMode(e, t.mode), Q.addTypeIds(e, i), Q.endUnion(e);
  }
  visitDictionary(t, e) {
    const i = this.visit(t.indices, e);
    return Lt.startDictionaryEncoding(e), Lt.addId(e, BigInt(t.id)), Lt.addIsOrdered(e, t.isOrdered), i !== void 0 && Lt.addIndexType(e, i), Lt.endDictionaryEncoding(e);
  }
  visitFixedSizeBinary(t, e) {
    return hn.startFixedSizeBinary(e), hn.addByteWidth(e, t.byteWidth), hn.endFixedSizeBinary(e);
  }
  visitFixedSizeList(t, e) {
    return fn.startFixedSizeList(e), fn.addListSize(e, t.listSize), fn.endFixedSizeList(e);
  }
  visitMap(t, e) {
    return pn.startMap(e), pn.addKeysSorted(e, t.keysSorted), pn.endMap(e);
  }
}
const ni = new Vc();
function Rc(n, t = /* @__PURE__ */ new Map()) {
  return new C(kc(n, t), gn(n.metadata), t);
}
function Io(n) {
  return new ot(n.count, So(n.columns), Bo(n.columns), null);
}
function zc(n) {
  return new Ft(Io(n.data), n.id, n.isDelta);
}
function kc(n, t) {
  return (n.fields || []).filter(Boolean).map((e) => U.fromJSON(e, t));
}
function Is(n, t) {
  return (n.children || []).filter(Boolean).map((e) => U.fromJSON(e, t));
}
function So(n) {
  return (n || []).reduce((t, e) => [
    ...t,
    new Oe(e.count, Pc(e.VALIDITY)),
    ...So(e.children)
  ], []);
}
function Bo(n, t = []) {
  for (let e = -1, i = (n || []).length; ++e < i; ) {
    const s = n[e];
    s.VALIDITY && t.push(new yt(t.length, s.VALIDITY.length)), s.TYPE_ID && t.push(new yt(t.length, s.TYPE_ID.length)), s.OFFSET && t.push(new yt(t.length, s.OFFSET.length)), s.DATA && t.push(new yt(t.length, s.DATA.length)), t = Bo(s.children, t);
  }
  return t;
}
function Pc(n) {
  return (n || []).reduce((t, e) => t + +(e === 0), 0);
}
function jc(n, t) {
  let e, i, s, r, o, a;
  return !t || !(r = n.dictionary) ? (o = Bs(n, Is(n, t)), s = new U(n.name, o, n.nullable, gn(n.metadata))) : t.has(e = r.id) ? (i = (i = r.indexType) ? Ss(i) : new Yt(), a = new Wt(t.get(e), i, e, r.isOrdered), s = new U(n.name, a, n.nullable, gn(n.metadata))) : (i = (i = r.indexType) ? Ss(i) : new Yt(), t.set(e, o = Bs(n, Is(n, t))), a = new Wt(o, i, e, r.isOrdered), s = new U(n.name, a, n.nullable, gn(n.metadata))), s || null;
}
function gn(n = []) {
  return new Map(n.map(({ key: t, value: e }) => [t, e]));
}
function Ss(n) {
  return new tt(n.isSigned, n.bitWidth);
}
function Bs(n, t) {
  const e = n.type.name;
  switch (e) {
    case "NONE":
      return new xt();
    case "null":
      return new xt();
    case "binary":
      return new vn();
    case "largebinary":
      return new wn();
    case "utf8":
      return new We();
    case "largeutf8":
      return new In();
    case "bool":
      return new He();
    case "list":
      return new we((t || [])[0]);
    case "struct":
      return new H(t || []);
    case "struct_":
      return new H(t || []);
  }
  switch (e) {
    case "int": {
      const i = n.type;
      return new tt(i.isSigned, i.bitWidth);
    }
    case "floatingpoint": {
      const i = n.type;
      return new ve(W[i.precision]);
    }
    case "decimal": {
      const i = n.type;
      return new Sn(i.scale, i.precision, i.bitWidth);
    }
    case "date": {
      const i = n.type;
      return new Bn(dt[i.unit]);
    }
    case "time": {
      const i = n.type;
      return new An(b[i.unit], i.bitWidth);
    }
    case "timestamp": {
      const i = n.type;
      return new Je(b[i.unit], i.timezone);
    }
    case "interval": {
      const i = n.type;
      return new Dn(J[i.unit]);
    }
    case "duration": {
      const i = n.type;
      return new On(b[i.unit]);
    }
    case "union": {
      const i = n.type, [s, ...r] = (i.mode + "").toLowerCase(), o = s.toUpperCase() + r.join("");
      return new Ke(X[o], i.typeIds || [], t || []);
    }
    case "fixedsizebinary": {
      const i = n.type;
      return new Fn(i.byteWidth);
    }
    case "fixedsizelist": {
      const i = n.type;
      return new Ge(i.listSize, (t || [])[0]);
    }
    case "map": {
      const i = n.type;
      return new qe((t || [])[0], i.keysSorted);
    }
  }
  throw new Error(`Unrecognized type: "${e}"`);
}
var $c = Ps, Yc = Qt;
class pt {
  /** @nocollapse */
  static fromJSON(t, e) {
    const i = new pt(0, $.V5, e);
    return i._createHeader = Wc(t, e), i;
  }
  /** @nocollapse */
  static decode(t) {
    t = new Yc(N(t));
    const e = Pt.getRootAsMessage(t), i = e.bodyLength(), s = e.version(), r = e.headerType(), o = new pt(i, s, r);
    return o._createHeader = Hc(e, r), o;
  }
  /** @nocollapse */
  static encode(t) {
    const e = new $c();
    let i = -1;
    return t.isSchema() ? i = C.encode(e, t.header()) : t.isRecordBatch() ? i = ot.encode(e, t.header()) : t.isDictionaryBatch() && (i = Ft.encode(e, t.header())), Pt.startMessage(e), Pt.addVersion(e, $.V5), Pt.addHeader(e, i), Pt.addHeaderType(e, t.headerType), Pt.addBodyLength(e, BigInt(t.bodyLength)), Pt.finishMessageBuffer(e, Pt.endMessage(e)), e.asUint8Array();
  }
  /** @nocollapse */
  static from(t, e = 0) {
    if (t instanceof C)
      return new pt(0, $.V5, x.Schema, t);
    if (t instanceof ot)
      return new pt(e, $.V5, x.RecordBatch, t);
    if (t instanceof Ft)
      return new pt(e, $.V5, x.DictionaryBatch, t);
    throw new Error(`Unrecognized Message header: ${t}`);
  }
  get type() {
    return this.headerType;
  }
  get version() {
    return this._version;
  }
  get headerType() {
    return this._headerType;
  }
  get compression() {
    return this._compression;
  }
  get bodyLength() {
    return this._bodyLength;
  }
  header() {
    return this._createHeader();
  }
  isSchema() {
    return this.headerType === x.Schema;
  }
  isRecordBatch() {
    return this.headerType === x.RecordBatch;
  }
  isDictionaryBatch() {
    return this.headerType === x.DictionaryBatch;
  }
  constructor(t, e, i, s) {
    this._version = e, this._headerType = i, this.body = new Uint8Array(0), this._compression = s?.compression, s && (this._createHeader = () => s), this._bodyLength = k(t);
  }
}
let ot = class {
  get nodes() {
    return this._nodes;
  }
  get length() {
    return this._length;
  }
  get buffers() {
    return this._buffers;
  }
  get compression() {
    return this._compression;
  }
  constructor(t, e, i, s) {
    this._nodes = e, this._buffers = i, this._length = k(t), this._compression = s;
  }
};
class Ft {
  get id() {
    return this._id;
  }
  get data() {
    return this._data;
  }
  get isDelta() {
    return this._isDelta;
  }
  get length() {
    return this.data.length;
  }
  get nodes() {
    return this.data.nodes;
  }
  get buffers() {
    return this.data.buffers;
  }
  constructor(t, e, i = !1) {
    this._data = t, this._isDelta = i, this._id = k(e);
  }
}
class yt {
  constructor(t, e) {
    this.offset = k(t), this.length = k(e);
  }
}
class Oe {
  constructor(t, e) {
    this.length = k(t), this.nullCount = k(e);
  }
}
class Ki {
  constructor(t, e = je.BUFFER) {
    this.type = t, this.method = e;
  }
}
function Wc(n, t) {
  return (() => {
    switch (t) {
      case x.Schema:
        return C.fromJSON(n);
      case x.RecordBatch:
        return ot.fromJSON(n);
      case x.DictionaryBatch:
        return Ft.fromJSON(n);
    }
    throw new Error(`Unrecognized Message type: { name: ${x[t]}, type: ${t} }`);
  });
}
function Hc(n, t) {
  return (() => {
    switch (t) {
      case x.Schema:
        return C.decode(n.header(new vt()), /* @__PURE__ */ new Map(), n.version());
      case x.RecordBatch:
        return ot.decode(n.header(new _t()), n.version());
      case x.DictionaryBatch:
        return Ft.decode(n.header(new ie()), n.version());
    }
    throw new Error(`Unrecognized Message type: { name: ${x[t]}, type: ${t} }`);
  });
}
U.encode = il;
U.decode = el;
U.fromJSON = jc;
C.encode = nl;
C.decode = Jc;
C.fromJSON = Rc;
ot.encode = sl;
ot.decode = Kc;
ot.fromJSON = Io;
Ft.encode = rl;
Ft.decode = Gc;
Ft.fromJSON = zc;
Oe.encode = ol;
Oe.decode = Zc;
yt.encode = al;
yt.decode = qc;
Ki.encode = Do;
Ki.decode = Ao;
function Jc(n, t = /* @__PURE__ */ new Map(), e = $.V5) {
  const i = tl(n, t);
  return new C(i, mn(n), t, e);
}
function Kc(n, t = $.V5) {
  return new ot(n.length(), Qc(n), Xc(n, t), Ao(n.compression()));
}
function Gc(n, t = $.V5) {
  return new Ft(ot.decode(n.data(), t), n.id(), n.isDelta());
}
function qc(n) {
  return new yt(n.offset(), n.length());
}
function Zc(n) {
  return new Oe(n.length(), n.nullCount());
}
function Qc(n) {
  const t = [];
  for (let e, i = -1, s = -1, r = n.nodesLength(); ++i < r; )
    (e = n.nodes(i)) && (t[++s] = Oe.decode(e));
  return t;
}
function Xc(n, t) {
  const e = [];
  for (let i, s = -1, r = -1, o = n.buffersLength(); ++s < o; )
    (i = n.buffers(s)) && (t < $.V4 && (i.bb_pos += 8 * (s + 1)), e[++r] = yt.decode(i));
  return e;
}
function tl(n, t) {
  const e = [];
  for (let i, s = -1, r = -1, o = n.fieldsLength(); ++s < o; )
    (i = n.fields(s)) && (e[++r] = U.decode(i, t));
  return e;
}
function As(n, t) {
  const e = [];
  for (let i, s = -1, r = -1, o = n.childrenLength(); ++s < o; )
    (i = n.children(s)) && (e[++r] = U.decode(i, t));
  return e;
}
function el(n, t) {
  let e, i, s, r, o, a;
  return !t || !(a = n.dictionary()) ? (s = Os(n, As(n, t)), i = new U(n.name(), s, n.nullable(), mn(n))) : t.has(e = k(a.id())) ? (r = (r = a.indexType()) ? Ds(r) : new Yt(), o = new Wt(t.get(e), r, e, a.isOrdered()), i = new U(n.name(), o, n.nullable(), mn(n))) : (r = (r = a.indexType()) ? Ds(r) : new Yt(), t.set(e, s = Os(n, As(n, t))), o = new Wt(s, r, e, a.isOrdered()), i = new U(n.name(), o, n.nullable(), mn(n))), i || null;
}
function mn(n) {
  const t = /* @__PURE__ */ new Map();
  if (n)
    for (let e, i, s = -1, r = Math.trunc(n.customMetadataLength()); ++s < r; )
      (e = n.customMetadata(s)) && (i = e.key()) != null && t.set(i, e.value());
  return t;
}
function Ds(n) {
  return new tt(n.isSigned(), n.bitWidth());
}
function Os(n, t) {
  const e = n.typeType();
  switch (e) {
    case z.NONE:
      return new xt();
    case z.Null:
      return new xt();
    case z.Binary:
      return new vn();
    case z.LargeBinary:
      return new wn();
    case z.Utf8:
      return new We();
    case z.LargeUtf8:
      return new In();
    case z.Bool:
      return new He();
    case z.List:
      return new we((t || [])[0]);
    case z.Struct_:
      return new H(t || []);
  }
  switch (e) {
    case z.Int: {
      const i = n.type(new st());
      return new tt(i.isSigned(), i.bitWidth());
    }
    case z.FloatingPoint: {
      const i = n.type(new Bt());
      return new ve(i.precision());
    }
    case z.Decimal: {
      const i = n.type(new re());
      return new Sn(i.scale(), i.precision(), i.bitWidth());
    }
    case z.Date: {
      const i = n.type(new un());
      return new Bn(i.unit());
    }
    case z.Time: {
      const i = n.type(new lt());
      return new An(i.unit(), i.bitWidth());
    }
    case z.Timestamp: {
      const i = n.type(new ut());
      return new Je(i.unit(), i.timezone());
    }
    case z.Interval: {
      const i = n.type(new At());
      return new Dn(i.unit());
    }
    case z.Duration: {
      const i = n.type(new dn());
      return new On(i.unit());
    }
    case z.Union: {
      const i = n.type(new Q());
      return new Ke(i.mode(), i.typeIdsArray() || [], t || []);
    }
    case z.FixedSizeBinary: {
      const i = n.type(new hn());
      return new Fn(i.byteWidth());
    }
    case z.FixedSizeList: {
      const i = n.type(new fn());
      return new Ge(i.listSize(), (t || [])[0]);
    }
    case z.Map: {
      const i = n.type(new pn());
      return new qe((t || [])[0], i.keysSorted());
    }
  }
  throw new Error(`Unrecognized type: "${z[e]}" (${e})`);
}
function Ao(n) {
  return n ? new Ki(n.codec(), n.method()) : null;
}
function nl(n, t) {
  const e = t.fields.map((r) => U.encode(n, r));
  vt.startFieldsVector(n, e.length);
  const i = vt.createFieldsVector(n, e), s = t.metadata && t.metadata.size > 0 ? vt.createCustomMetadataVector(n, [...t.metadata].map(([r, o]) => {
    const a = n.createString(`${r}`), c = n.createString(`${o}`);
    return Y.startKeyValue(n), Y.addKey(n, a), Y.addValue(n, c), Y.endKeyValue(n);
  })) : -1;
  return vt.startSchema(n), vt.addFields(n, i), vt.addEndianness(n, cl ? _e.Little : _e.Big), s !== -1 && vt.addCustomMetadata(n, s), vt.endSchema(n);
}
function il(n, t) {
  let e = -1, i = -1, s = -1;
  const r = t.type;
  let o = t.typeId;
  f.isDictionary(r) ? (o = r.dictionary.typeId, s = ni.visit(r, n), i = ni.visit(r.dictionary, n)) : i = ni.visit(r, n);
  const a = (r.children || []).map((d) => U.encode(n, d)), c = at.createChildrenVector(n, a), u = t.metadata && t.metadata.size > 0 ? at.createCustomMetadataVector(n, [...t.metadata].map(([d, h]) => {
    const T = n.createString(`${d}`), O = n.createString(`${h}`);
    return Y.startKeyValue(n), Y.addKey(n, T), Y.addValue(n, O), Y.endKeyValue(n);
  })) : -1;
  return t.name && (e = n.createString(t.name)), at.startField(n), at.addType(n, i), at.addTypeType(n, o), at.addChildren(n, c), at.addNullable(n, !!t.nullable), e !== -1 && at.addName(n, e), s !== -1 && at.addDictionary(n, s), u !== -1 && at.addCustomMetadata(n, u), at.endField(n);
}
function sl(n, t) {
  const e = t.nodes || [], i = t.buffers || [];
  _t.startNodesVector(n, e.length);
  for (const a of e.slice().reverse())
    Oe.encode(n, a);
  const s = n.endVector();
  _t.startBuffersVector(n, i.length);
  for (const a of i.slice().reverse())
    yt.encode(n, a);
  const r = n.endVector();
  let o = null;
  return t.compression !== null && (o = Do(n, t.compression)), _t.startRecordBatch(n), _t.addLength(n, BigInt(t.length)), _t.addNodes(n, s), _t.addBuffers(n, r), t.compression !== null && o && _t.addCompression(n, o), _t.endRecordBatch(n);
}
function Do(n, t) {
  return Le.startBodyCompression(n), Le.addCodec(n, t.type), Le.addMethod(n, t.method), Le.endBodyCompression(n);
}
function rl(n, t) {
  const e = ot.encode(n, t.data);
  return ie.startDictionaryBatch(n), ie.addId(n, BigInt(t.id)), ie.addIsDelta(n, t.isDelta), ie.addData(n, e), ie.endDictionaryBatch(n);
}
function ol(n, t) {
  return Ys.createFieldNode(n, BigInt(t.length), BigInt(t.nullCount));
}
function al(n, t) {
  return $s.createBuffer(n, BigInt(t.offset), BigInt(t.length));
}
const cl = (() => {
  const n = new ArrayBuffer(2);
  return new DataView(n).setInt16(
    0,
    256,
    !0
    /* littleEndian */
  ), new Int16Array(n)[0] === 256;
})(), P = Object.freeze({ done: !0, value: void 0 });
class Fs {
  constructor(t) {
    this._json = t;
  }
  get schema() {
    return this._json.schema;
  }
  get batches() {
    return this._json.batches || [];
  }
  get dictionaries() {
    return this._json.dictionaries || [];
  }
}
class Oo {
  tee() {
    return this._getDOMStream().tee();
  }
  pipe(t, e) {
    return this._getNodeStream().pipe(t, e);
  }
  pipeTo(t, e) {
    return this._getDOMStream().pipeTo(t, e);
  }
  pipeThrough(t, e) {
    return this._getDOMStream().pipeThrough(t, e);
  }
  _getDOMStream() {
    return this._DOMStream || (this._DOMStream = this.toDOMStream());
  }
  _getNodeStream() {
    return this._nodeStream || (this._nodeStream = this.toNodeStream());
  }
}
class ll extends Oo {
  constructor() {
    super(), this._values = [], this.resolvers = [], this._closedPromise = new Promise((t) => this._closedPromiseResolve = t);
  }
  get closed() {
    return this._closedPromise;
  }
  cancel(t) {
    return D(this, void 0, void 0, function* () {
      yield this.return(t);
    });
  }
  write(t) {
    this._ensureOpen() && (this.resolvers.length <= 0 ? this._values.push(t) : this.resolvers.shift().resolve({ done: !1, value: t }));
  }
  abort(t) {
    this._closedPromiseResolve && (this.resolvers.length <= 0 ? this._error = { error: t } : this.resolvers.shift().reject({ done: !0, value: t }));
  }
  close() {
    if (this._closedPromiseResolve) {
      const { resolvers: t } = this;
      for (; t.length > 0; )
        t.shift().resolve(P);
      this._closedPromiseResolve(), this._closedPromiseResolve = void 0;
    }
  }
  [Symbol.asyncIterator]() {
    return this;
  }
  toDOMStream(t) {
    return ct.toDOMStream(this._closedPromiseResolve || this._error ? this : this._values, t);
  }
  toNodeStream(t) {
    return ct.toNodeStream(this._closedPromiseResolve || this._error ? this : this._values, t);
  }
  throw(t) {
    return D(this, void 0, void 0, function* () {
      return yield this.abort(t), P;
    });
  }
  return(t) {
    return D(this, void 0, void 0, function* () {
      return yield this.close(), P;
    });
  }
  read(t) {
    return D(this, void 0, void 0, function* () {
      return (yield this.next(t, "read")).value;
    });
  }
  peek(t) {
    return D(this, void 0, void 0, function* () {
      return (yield this.next(t, "peek")).value;
    });
  }
  next(...t) {
    return this._values.length > 0 ? Promise.resolve({ done: !1, value: this._values.shift() }) : this._error ? Promise.reject({ done: !0, value: this._error.error }) : this._closedPromiseResolve ? new Promise((e, i) => {
      this.resolvers.push({ resolve: e, reject: i });
    }) : Promise.resolve(P);
  }
  _ensureOpen() {
    if (this._closedPromiseResolve)
      return !0;
    throw new Error("AsyncQueue is closed");
  }
}
class ul extends ll {
  write(t) {
    if ((t = N(t)).byteLength > 0)
      return super.write(t);
  }
  toString(t = !1) {
    return t ? ri(this.toUint8Array(!0)) : this.toUint8Array(!1).then(ri);
  }
  toUint8Array(t = !1) {
    return t ? Ot(this._values)[0] : D(this, void 0, void 0, function* () {
      var e, i, s, r;
      const o = [];
      let a = 0;
      try {
        for (var c = !0, u = pe(this), d; d = yield u.next(), e = d.done, !e; c = !0) {
          r = d.value, c = !1;
          const h = r;
          o.push(h), a += h.byteLength;
        }
      } catch (h) {
        i = { error: h };
      } finally {
        try {
          !c && !e && (s = u.return) && (yield s.call(u));
        } finally {
          if (i) throw i.error;
        }
      }
      return Ot(o, a)[0];
    });
  }
}
class Ln {
  constructor(t) {
    t && (this.source = new dl(ct.fromIterable(t)));
  }
  [Symbol.iterator]() {
    return this;
  }
  next(t) {
    return this.source.next(t);
  }
  throw(t) {
    return this.source.throw(t);
  }
  return(t) {
    return this.source.return(t);
  }
  peek(t) {
    return this.source.peek(t);
  }
  read(t) {
    return this.source.read(t);
  }
}
class Se {
  constructor(t) {
    t instanceof Se ? this.source = t.source : t instanceof ul ? this.source = new Kt(ct.fromAsyncIterable(t)) : Rs(t) ? this.source = new Kt(ct.fromNodeStream(t)) : _i(t) ? this.source = new Kt(ct.fromDOMStream(t)) : Es(t) ? this.source = new Kt(ct.fromDOMStream(t.body)) : Pn(t) ? this.source = new Kt(ct.fromIterable(t)) : Pe(t) ? this.source = new Kt(ct.fromAsyncIterable(t)) : bi(t) && (this.source = new Kt(ct.fromAsyncIterable(t)));
  }
  [Symbol.asyncIterator]() {
    return this;
  }
  next(t) {
    return this.source.next(t);
  }
  throw(t) {
    return this.source.throw(t);
  }
  return(t) {
    return this.source.return(t);
  }
  get closed() {
    return this.source.closed;
  }
  cancel(t) {
    return this.source.cancel(t);
  }
  peek(t) {
    return this.source.peek(t);
  }
  read(t) {
    return this.source.read(t);
  }
}
class dl {
  constructor(t) {
    this.source = t;
  }
  cancel(t) {
    this.return(t);
  }
  peek(t) {
    return this.next(t, "peek").value;
  }
  read(t) {
    return this.next(t, "read").value;
  }
  next(t, e = "read") {
    return this.source.next({ cmd: e, size: t });
  }
  throw(t) {
    return Object.create(this.source.throw && this.source.throw(t) || P);
  }
  return(t) {
    return Object.create(this.source.return && this.source.return(t) || P);
  }
}
class Kt {
  constructor(t) {
    this.source = t, this._closedPromise = new Promise((e) => this._closedPromiseResolve = e);
  }
  cancel(t) {
    return D(this, void 0, void 0, function* () {
      yield this.return(t);
    });
  }
  get closed() {
    return this._closedPromise;
  }
  read(t) {
    return D(this, void 0, void 0, function* () {
      return (yield this.next(t, "read")).value;
    });
  }
  peek(t) {
    return D(this, void 0, void 0, function* () {
      return (yield this.next(t, "peek")).value;
    });
  }
  next(t) {
    return D(this, arguments, void 0, function* (e, i = "read") {
      return yield this.source.next({ cmd: i, size: e });
    });
  }
  throw(t) {
    return D(this, void 0, void 0, function* () {
      const e = this.source.throw && (yield this.source.throw(t)) || P;
      return this._closedPromiseResolve && this._closedPromiseResolve(), this._closedPromiseResolve = void 0, Object.create(e);
    });
  }
  return(t) {
    return D(this, void 0, void 0, function* () {
      const e = this.source.return && (yield this.source.return(t)) || P;
      return this._closedPromiseResolve && this._closedPromiseResolve(), this._closedPromiseResolve = void 0, Object.create(e);
    });
  }
}
class Ms extends Ln {
  constructor(t, e) {
    super(), this.position = 0, this.buffer = N(t), this.size = e === void 0 ? this.buffer.byteLength : e;
  }
  readInt32(t) {
    const { buffer: e, byteOffset: i } = this.readAt(t, 4);
    return new DataView(e, i).getInt32(0, !0);
  }
  seek(t) {
    return this.position = Math.min(t, this.size), t < this.size;
  }
  read(t) {
    const { buffer: e, size: i, position: s } = this;
    return e && s < i ? (typeof t != "number" && (t = Number.POSITIVE_INFINITY), this.position = Math.min(i, s + Math.min(i - s, t)), e.subarray(s, this.position)) : null;
  }
  readAt(t, e) {
    const i = this.buffer, s = Math.min(this.size, t + e);
    return i ? i.subarray(t, s) : new Uint8Array(e);
  }
  close() {
    this.buffer && (this.buffer = null);
  }
  throw(t) {
    return this.close(), { done: !0, value: t };
  }
  return(t) {
    return this.close(), { done: !0, value: t };
  }
}
class Un extends Se {
  constructor(t, e) {
    super(), this.position = 0, this._handle = t, typeof e == "number" ? this.size = e : this._pending = D(this, void 0, void 0, function* () {
      this.size = (yield t.stat()).size, delete this._pending;
    });
  }
  readInt32(t) {
    return D(this, void 0, void 0, function* () {
      const { buffer: e, byteOffset: i } = yield this.readAt(t, 4);
      return new DataView(e, i).getInt32(0, !0);
    });
  }
  seek(t) {
    return D(this, void 0, void 0, function* () {
      return this._pending && (yield this._pending), this.position = Math.min(t, this.size), t < this.size;
    });
  }
  read(t) {
    return D(this, void 0, void 0, function* () {
      this._pending && (yield this._pending);
      const { _handle: e, size: i, position: s } = this;
      if (e && s < i) {
        typeof t != "number" && (t = Number.POSITIVE_INFINITY);
        let r = s, o = 0, a = 0;
        const c = Math.min(i, r + Math.min(i - r, t)), u = new Uint8Array(Math.max(0, (this.position = c) - r));
        for (; (r += a) < c && (o += a) < u.byteLength; )
          ({ bytesRead: a } = yield e.read(u, o, u.byteLength - o, r));
        return u;
      }
      return null;
    });
  }
  readAt(t, e) {
    return D(this, void 0, void 0, function* () {
      this._pending && (yield this._pending);
      const { _handle: i, size: s } = this;
      if (i && t + e < s) {
        const r = Math.min(s, t + e), o = new Uint8Array(r - t);
        return (yield i.read(o, 0, e, t)).buffer;
      }
      return new Uint8Array(e);
    });
  }
  close() {
    return D(this, void 0, void 0, function* () {
      const t = this._handle;
      this._handle = null, t && (yield t.close());
    });
  }
  throw(t) {
    return D(this, void 0, void 0, function* () {
      return yield this.close(), { done: !0, value: t };
    });
  }
  return(t) {
    return D(this, void 0, void 0, function* () {
      return yield this.close(), { done: !0, value: t };
    });
  }
}
const hl = 65536;
function fe(n) {
  return n < 0 && (n = 4294967295 + n + 1), `0x${n.toString(16)}`;
}
const Be = 8, Gi = [
  1,
  10,
  100,
  1e3,
  1e4,
  1e5,
  1e6,
  1e7,
  1e8
];
class Fo {
  constructor(t) {
    this.buffer = t;
  }
  high() {
    return this.buffer[1];
  }
  low() {
    return this.buffer[0];
  }
  _times(t) {
    const e = new Uint32Array([
      this.buffer[1] >>> 16,
      this.buffer[1] & 65535,
      this.buffer[0] >>> 16,
      this.buffer[0] & 65535
    ]), i = new Uint32Array([
      t.buffer[1] >>> 16,
      t.buffer[1] & 65535,
      t.buffer[0] >>> 16,
      t.buffer[0] & 65535
    ]);
    let s = e[3] * i[3];
    this.buffer[0] = s & 65535;
    let r = s >>> 16;
    return s = e[2] * i[3], r += s, s = e[3] * i[2] >>> 0, r += s, this.buffer[0] += r << 16, this.buffer[1] = r >>> 0 < s ? hl : 0, this.buffer[1] += r >>> 16, this.buffer[1] += e[1] * i[3] + e[2] * i[2] + e[3] * i[1], this.buffer[1] += e[0] * i[3] + e[1] * i[2] + e[2] * i[1] + e[3] * i[0] << 16, this;
  }
  _plus(t) {
    const e = this.buffer[0] + t.buffer[0] >>> 0;
    this.buffer[1] += t.buffer[1], e < this.buffer[0] >>> 0 && ++this.buffer[1], this.buffer[0] = e;
  }
  lessThan(t) {
    return this.buffer[1] < t.buffer[1] || this.buffer[1] === t.buffer[1] && this.buffer[0] < t.buffer[0];
  }
  equals(t) {
    return this.buffer[1] === t.buffer[1] && this.buffer[0] == t.buffer[0];
  }
  greaterThan(t) {
    return t.lessThan(this);
  }
  hex() {
    return `${fe(this.buffer[1])} ${fe(this.buffer[0])}`;
  }
}
class V extends Fo {
  times(t) {
    return this._times(t), this;
  }
  plus(t) {
    return this._plus(t), this;
  }
  /** @nocollapse */
  static from(t, e = new Uint32Array(2)) {
    return V.fromString(typeof t == "string" ? t : t.toString(), e);
  }
  /** @nocollapse */
  static fromNumber(t, e = new Uint32Array(2)) {
    return V.fromString(t.toString(), e);
  }
  /** @nocollapse */
  static fromString(t, e = new Uint32Array(2)) {
    const i = t.length, s = new V(e);
    for (let r = 0; r < i; ) {
      const o = Be < i - r ? Be : i - r, a = new V(new Uint32Array([Number.parseInt(t.slice(r, r + o), 10), 0])), c = new V(new Uint32Array([Gi[o], 0]));
      s.times(c), s.plus(a), r += o;
    }
    return s;
  }
  /** @nocollapse */
  static convertArray(t) {
    const e = new Uint32Array(t.length * 2);
    for (let i = -1, s = t.length; ++i < s; )
      V.from(t[i], new Uint32Array(e.buffer, e.byteOffset + 2 * i * 4, 2));
    return e;
  }
  /** @nocollapse */
  static multiply(t, e) {
    return new V(new Uint32Array(t.buffer)).times(e);
  }
  /** @nocollapse */
  static add(t, e) {
    return new V(new Uint32Array(t.buffer)).plus(e);
  }
}
class Z extends Fo {
  negate() {
    return this.buffer[0] = ~this.buffer[0] + 1, this.buffer[1] = ~this.buffer[1], this.buffer[0] == 0 && ++this.buffer[1], this;
  }
  times(t) {
    return this._times(t), this;
  }
  plus(t) {
    return this._plus(t), this;
  }
  lessThan(t) {
    const e = this.buffer[1] << 0, i = t.buffer[1] << 0;
    return e < i || e === i && this.buffer[0] < t.buffer[0];
  }
  /** @nocollapse */
  static from(t, e = new Uint32Array(2)) {
    return Z.fromString(typeof t == "string" ? t : t.toString(), e);
  }
  /** @nocollapse */
  static fromNumber(t, e = new Uint32Array(2)) {
    return Z.fromString(t.toString(), e);
  }
  /** @nocollapse */
  static fromString(t, e = new Uint32Array(2)) {
    const i = t.startsWith("-"), s = t.length, r = new Z(e);
    for (let o = i ? 1 : 0; o < s; ) {
      const a = Be < s - o ? Be : s - o, c = new Z(new Uint32Array([Number.parseInt(t.slice(o, o + a), 10), 0])), u = new Z(new Uint32Array([Gi[a], 0]));
      r.times(u), r.plus(c), o += a;
    }
    return i ? r.negate() : r;
  }
  /** @nocollapse */
  static convertArray(t) {
    const e = new Uint32Array(t.length * 2);
    for (let i = -1, s = t.length; ++i < s; )
      Z.from(t[i], new Uint32Array(e.buffer, e.byteOffset + 2 * i * 4, 2));
    return e;
  }
  /** @nocollapse */
  static multiply(t, e) {
    return new Z(new Uint32Array(t.buffer)).times(e);
  }
  /** @nocollapse */
  static add(t, e) {
    return new Z(new Uint32Array(t.buffer)).plus(e);
  }
}
class wt {
  constructor(t) {
    this.buffer = t;
  }
  high() {
    return new Z(new Uint32Array(this.buffer.buffer, this.buffer.byteOffset + 8, 2));
  }
  low() {
    return new Z(new Uint32Array(this.buffer.buffer, this.buffer.byteOffset, 2));
  }
  negate() {
    return this.buffer[0] = ~this.buffer[0] + 1, this.buffer[1] = ~this.buffer[1], this.buffer[2] = ~this.buffer[2], this.buffer[3] = ~this.buffer[3], this.buffer[0] == 0 && ++this.buffer[1], this.buffer[1] == 0 && ++this.buffer[2], this.buffer[2] == 0 && ++this.buffer[3], this;
  }
  times(t) {
    const e = new V(new Uint32Array([this.buffer[3], 0])), i = new V(new Uint32Array([this.buffer[2], 0])), s = new V(new Uint32Array([this.buffer[1], 0])), r = new V(new Uint32Array([this.buffer[0], 0])), o = new V(new Uint32Array([t.buffer[3], 0])), a = new V(new Uint32Array([t.buffer[2], 0])), c = new V(new Uint32Array([t.buffer[1], 0])), u = new V(new Uint32Array([t.buffer[0], 0]));
    let d = V.multiply(r, u);
    this.buffer[0] = d.low();
    const h = new V(new Uint32Array([d.high(), 0]));
    return d = V.multiply(s, u), h.plus(d), d = V.multiply(r, c), h.plus(d), this.buffer[1] = h.low(), this.buffer[3] = h.lessThan(d) ? 1 : 0, this.buffer[2] = h.high(), new V(new Uint32Array(this.buffer.buffer, this.buffer.byteOffset + 8, 2)).plus(V.multiply(i, u)).plus(V.multiply(s, c)).plus(V.multiply(r, a)), this.buffer[3] += V.multiply(e, u).plus(V.multiply(i, c)).plus(V.multiply(s, a)).plus(V.multiply(r, o)).low(), this;
  }
  plus(t) {
    const e = new Uint32Array(4);
    return e[3] = this.buffer[3] + t.buffer[3] >>> 0, e[2] = this.buffer[2] + t.buffer[2] >>> 0, e[1] = this.buffer[1] + t.buffer[1] >>> 0, e[0] = this.buffer[0] + t.buffer[0] >>> 0, e[0] < this.buffer[0] >>> 0 && ++e[1], e[1] < this.buffer[1] >>> 0 && ++e[2], e[2] < this.buffer[2] >>> 0 && ++e[3], this.buffer[3] = e[3], this.buffer[2] = e[2], this.buffer[1] = e[1], this.buffer[0] = e[0], this;
  }
  hex() {
    return `${fe(this.buffer[3])} ${fe(this.buffer[2])} ${fe(this.buffer[1])} ${fe(this.buffer[0])}`;
  }
  /** @nocollapse */
  static multiply(t, e) {
    return new wt(new Uint32Array(t.buffer)).times(e);
  }
  /** @nocollapse */
  static add(t, e) {
    return new wt(new Uint32Array(t.buffer)).plus(e);
  }
  /** @nocollapse */
  static from(t, e = new Uint32Array(4)) {
    return wt.fromString(typeof t == "string" ? t : t.toString(), e);
  }
  /** @nocollapse */
  static fromNumber(t, e = new Uint32Array(4)) {
    return wt.fromString(t.toString(), e);
  }
  /** @nocollapse */
  static fromString(t, e = new Uint32Array(4)) {
    const i = t.startsWith("-"), s = t.length, r = new wt(e);
    for (let o = i ? 1 : 0; o < s; ) {
      const a = Be < s - o ? Be : s - o, c = new wt(new Uint32Array([Number.parseInt(t.slice(o, o + a), 10), 0, 0, 0])), u = new wt(new Uint32Array([Gi[a], 0, 0, 0]));
      r.times(u), r.plus(c), o += a;
    }
    return i ? r.negate() : r;
  }
  /** @nocollapse */
  static convertArray(t) {
    const e = new Uint32Array(t.length * 4);
    for (let i = -1, s = t.length; ++i < s; )
      wt.from(t[i], new Uint32Array(e.buffer, e.byteOffset + 16 * i, 4));
    return e;
  }
}
function fl(n) {
  var t, e;
  const i = n.length, s = new Int32Array(i * 2);
  for (let r = 0, o = 0; r < i; r++) {
    const a = n[r];
    s[o++] = (t = a.days) !== null && t !== void 0 ? t : 0, s[o++] = (e = a.milliseconds) !== null && e !== void 0 ? e : 0;
  }
  return s;
}
function pl(n) {
  var t, e;
  const i = n.length, s = new Int32Array(i * 4);
  for (let r = 0, o = 0; r < i; r++) {
    const a = n[r];
    s[o++] = (t = a.months) !== null && t !== void 0 ? t : 0, s[o++] = (e = a.days) !== null && e !== void 0 ? e : 0;
    const c = a.nanoseconds;
    c ? (s[o++] = Number(BigInt(c) & BigInt(4294967295)), s[o++] = Number(BigInt(c) >> BigInt(32))) : o += 2;
  }
  return s;
}
class qi extends M {
  constructor(t, e, i, s, r = $.V5) {
    super(), this.nodesIndex = -1, this.buffersIndex = -1, this.bytes = t, this.nodes = e, this.buffers = i, this.dictionaries = s, this.metadataVersion = r;
  }
  visit(t) {
    return super.visit(t instanceof U ? t.type : t);
  }
  visitNull(t, { length: e } = this.nextFieldNode()) {
    return I({ type: t, length: e });
  }
  visitBool(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitInt(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitFloat(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitUtf8(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), data: this.readData(t) });
  }
  visitLargeUtf8(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), data: this.readData(t) });
  }
  visitBinary(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), data: this.readData(t) });
  }
  visitLargeBinary(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), data: this.readData(t) });
  }
  visitFixedSizeBinary(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitDate(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitTimestamp(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitTime(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitDecimal(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitList(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), child: this.visit(t.children[0]) });
  }
  visitStruct(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), children: this.visitMany(t.children) });
  }
  visitUnion(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return this.metadataVersion < $.V5 && this.readNullBitmap(t, i), t.mode === X.Sparse ? this.visitSparseUnion(t, { length: e, nullCount: i }) : this.visitDenseUnion(t, { length: e, nullCount: i });
  }
  visitDenseUnion(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, typeIds: this.readTypeIds(t), valueOffsets: this.readOffsets(t), children: this.visitMany(t.children) });
  }
  visitSparseUnion(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, typeIds: this.readTypeIds(t), children: this.visitMany(t.children) });
  }
  visitDictionary(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t.indices), dictionary: this.readDictionary(t) });
  }
  visitInterval(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitDuration(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), data: this.readData(t) });
  }
  visitFixedSizeList(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), child: this.visit(t.children[0]) });
  }
  visitMap(t, { length: e, nullCount: i } = this.nextFieldNode()) {
    return I({ type: t, length: e, nullCount: i, nullBitmap: this.readNullBitmap(t, i), valueOffsets: this.readOffsets(t), child: this.visit(t.children[0]) });
  }
  nextFieldNode() {
    return this.nodes[++this.nodesIndex];
  }
  nextBufferRange() {
    return this.buffers[++this.buffersIndex];
  }
  readNullBitmap(t, e, i = this.nextBufferRange()) {
    return e > 0 && this.readData(t, i) || new Uint8Array(0);
  }
  readOffsets(t, e) {
    return this.readData(t, e);
  }
  readTypeIds(t, e) {
    return this.readData(t, e);
  }
  readData(t, { length: e, offset: i } = this.nextBufferRange()) {
    return this.bytes.subarray(i, i + e);
  }
  readDictionary(t) {
    return this.dictionaries.get(t.id);
  }
}
class yl extends qi {
  constructor(t, e, i, s, r) {
    super(new Uint8Array(0), e, i, s, r), this.sources = t;
  }
  readNullBitmap(t, e, { offset: i } = this.nextBufferRange()) {
    return e <= 0 ? new Uint8Array(0) : fi(this.sources[i]);
  }
  readOffsets(t, { offset: e } = this.nextBufferRange()) {
    return R(Uint8Array, R(t.OffsetArrayType, this.sources[e]));
  }
  readTypeIds(t, { offset: e } = this.nextBufferRange()) {
    return R(Uint8Array, R(t.ArrayType, this.sources[e]));
  }
  readData(t, { offset: e } = this.nextBufferRange()) {
    const { sources: i } = this;
    if (f.isTimestamp(t))
      return R(Uint8Array, Z.convertArray(i[e]));
    if ((f.isInt(t) || f.isTime(t)) && t.bitWidth === 64 || f.isDuration(t))
      return R(Uint8Array, Z.convertArray(i[e]));
    if (f.isDate(t) && t.unit === dt.MILLISECOND)
      return R(Uint8Array, Z.convertArray(i[e]));
    if (f.isDecimal(t))
      return R(Uint8Array, wt.convertArray(i[e]));
    if (f.isBinary(t) || f.isLargeBinary(t) || f.isFixedSizeBinary(t))
      return gl(i[e]);
    if (f.isBool(t))
      return fi(i[e]);
    if (f.isUtf8(t) || f.isLargeUtf8(t))
      return Ze(i[e].join(""));
    if (f.isInterval(t))
      switch (t.unit) {
        case J.DAY_TIME:
          return fl(i[e]);
        case J.MONTH_DAY_NANO:
          return pl(i[e]);
      }
    return R(Uint8Array, R(t.ArrayType, i[e].map((s) => +s)));
  }
}
function gl(n) {
  const t = n.join(""), e = new Uint8Array(t.length / 2);
  for (let i = 0; i < t.length; i += 2)
    e[i >> 1] = Number.parseInt(t.slice(i, i + 2), 16);
  return e;
}
class ml extends qi {
  constructor(t, e, i, s, r) {
    super(new Uint8Array(0), e, i, s, r), this.bodyChunks = t;
  }
  readData(t, e = this.nextBufferRange()) {
    return this.bodyChunks[this.buffersIndex];
  }
}
class Mo extends De {
  constructor(t) {
    super(t), this._values = new Qe(Uint8Array);
  }
  get byteLength() {
    let t = this._pendingLength + this.length * 4;
    return this._offsets && (t += this._offsets.byteLength), this._values && (t += this._values.byteLength), this._nulls && (t += this._nulls.byteLength), t;
  }
  setValue(t, e) {
    return super.setValue(t, N(e));
  }
  _flushPending(t, e) {
    const i = this._offsets, s = this._values.reserve(e).buffer;
    let r = 0;
    for (const [o, a] of t)
      if (a === void 0)
        i.set(o, 0);
      else {
        const c = a.length;
        s.set(a, r), i.set(o, c), r += c;
      }
  }
}
class No extends De {
  constructor(t) {
    super(t), this._values = new Qe(Uint8Array);
  }
  get byteLength() {
    let t = this._pendingLength + this.length * 4;
    return this._offsets && (t += this._offsets.byteLength), this._values && (t += this._values.byteLength), this._nulls && (t += this._nulls.byteLength), t;
  }
  setValue(t, e) {
    return super.setValue(t, N(e));
  }
  _flushPending(t, e) {
    const i = this._offsets, s = this._values.reserve(e).buffer;
    let r = 0;
    for (const [o, a] of t)
      if (a === void 0)
        i.set(o, BigInt(0));
      else {
        const c = a.length;
        s.set(a, r), i.set(o, BigInt(c)), r += c;
      }
  }
}
class bl extends nt {
  constructor(t) {
    super(t), this._values = new vo();
  }
  setValue(t, e) {
    this._values.set(t, +e);
  }
}
class Wn extends Vt {
}
Wn.prototype._setValue = Nr;
class To extends Wn {
}
To.prototype._setValue = Bi;
class Lo extends Wn {
}
Lo.prototype._setValue = Ai;
class Uo extends Vt {
}
Uo.prototype._setValue = Ur;
class _l extends nt {
  constructor({ type: t, nullValues: e, dictionaryHashFunction: i }) {
    super({ type: new Wt(t.dictionary, t.indices, t.id, t.isOrdered) }), this._nulls = null, this._dictionaryOffset = 0, this._keysToIndices = /* @__PURE__ */ Object.create(null), this.indices = xn({ type: this.type.indices, nullValues: e }), this.dictionary = xn({ type: this.type.dictionary, nullValues: null }), typeof i == "function" && (this.valueToKey = i);
  }
  get values() {
    return this.indices.values;
  }
  get nullCount() {
    return this.indices.nullCount;
  }
  get nullBitmap() {
    return this.indices.nullBitmap;
  }
  get byteLength() {
    return this.indices.byteLength + this.dictionary.byteLength;
  }
  get reservedLength() {
    return this.indices.reservedLength + this.dictionary.reservedLength;
  }
  get reservedByteLength() {
    return this.indices.reservedByteLength + this.dictionary.reservedByteLength;
  }
  isValid(t) {
    return this.indices.isValid(t);
  }
  setValid(t, e) {
    const i = this.indices;
    return e = i.setValid(t, e), this.length = i.length, e;
  }
  setValue(t, e) {
    const i = this._keysToIndices, s = this.valueToKey(e);
    let r = i[s];
    return r === void 0 && (i[s] = r = this._dictionaryOffset + this.dictionary.append(e).length - 1), this.indices.setValue(t, r);
  }
  flush() {
    const t = this.type, e = this._dictionary, i = this.dictionary.toVector(), s = this.indices.flush().clone(t);
    return s.dictionary = e ? e.concat(i) : i, this.finished || (this._dictionaryOffset += i.length), this._dictionary = s.dictionary, this.clear(), s;
  }
  finish() {
    return this.indices.finish(), this.dictionary.finish(), this._dictionaryOffset = 0, this._keysToIndices = /* @__PURE__ */ Object.create(null), super.finish();
  }
  clear() {
    return this.indices.clear(), this.dictionary.clear(), super.clear();
  }
  valueToKey(t) {
    return typeof t == "string" ? t : `${t}`;
  }
}
class xo extends Vt {
}
xo.prototype._setValue = Or;
class vl extends nt {
  setValue(t, e) {
    const [i] = this.children, s = t * this.stride;
    for (let r = -1, o = this.stride; ++r < o; )
      i.set(s + r, e[r]);
  }
  setValid(t, e) {
    return super.setValid(t, e) || this.children[0].setValid((t + 1) * this.stride - 1, !1), e;
  }
  addChild(t, e = "0") {
    if (this.numChildren > 0)
      throw new Error("FixedSizeListBuilder can only have one child.");
    const i = this.children.push(t);
    return this.type = new Ge(this.type.listSize, new U(e, t.type, !0)), i;
  }
}
class Hn extends Vt {
  setValue(t, e) {
    this._values.set(t, e);
  }
}
class wl extends Hn {
  setValue(t, e) {
    super.setValue(t, Br(e));
  }
}
class Il extends Hn {
}
class Sl extends Hn {
}
class tn extends Vt {
}
tn.prototype._setValue = Er;
class Co extends tn {
}
Co.prototype._setValue = xi;
class Eo extends tn {
}
Eo.prototype._setValue = Ci;
class Vo extends tn {
}
Vo.prototype._setValue = Ei;
class Fe extends Vt {
}
Fe.prototype._setValue = Vr;
class Ro extends Fe {
}
Ro.prototype._setValue = Vi;
class zo extends Fe {
}
zo.prototype._setValue = Ri;
class ko extends Fe {
}
ko.prototype._setValue = zi;
class Po extends Fe {
}
Po.prototype._setValue = ki;
class Rt extends Vt {
  setValue(t, e) {
    this._values.set(t, e);
  }
}
class Bl extends Rt {
}
class Al extends Rt {
}
class Dl extends Rt {
}
class Ol extends Rt {
}
class Fl extends Rt {
}
class Ml extends Rt {
}
class Nl extends Rt {
}
class Tl extends Rt {
}
class Ll extends De {
  constructor(t) {
    super(t), this._offsets = new wo(t.type);
  }
  addChild(t, e = "0") {
    if (this.numChildren > 0)
      throw new Error("ListBuilder can only have one child.");
    return this.children[this.numChildren] = t, this.type = new we(new U(e, t.type, !0)), this.numChildren - 1;
  }
  _flushPending(t) {
    const e = this._offsets, [i] = this.children;
    for (const [s, r] of t)
      if (typeof r > "u")
        e.set(s, 0);
      else {
        const o = r, a = o.length, c = e.set(s, a).buffer[s];
        for (let u = -1; ++u < a; )
          i.set(c + u, o[u]);
      }
  }
}
class Ul extends De {
  set(t, e) {
    return super.set(t, e);
  }
  setValue(t, e) {
    const i = e instanceof Map ? e : new Map(Object.entries(e)), s = this._pending || (this._pending = /* @__PURE__ */ new Map()), r = s.get(t);
    r && (this._pendingLength -= r.size), this._pendingLength += i.size, s.set(t, i);
  }
  addChild(t, e = `${this.numChildren}`) {
    if (this.numChildren > 0)
      throw new Error("ListBuilder can only have one child.");
    return this.children[this.numChildren] = t, this.type = new qe(new U(e, t.type, !0), this.type.keysSorted), this.numChildren - 1;
  }
  _flushPending(t) {
    const e = this._offsets, [i] = this.children;
    for (const [s, r] of t)
      if (r === void 0)
        e.set(s, 0);
      else {
        let { [s]: o, [s + 1]: a } = e.set(s, r.size).buffer;
        for (const c of r.entries())
          if (i.set(o, c), ++o >= a)
            break;
      }
  }
}
class xl extends nt {
  // @ts-ignore
  setValue(t, e) {
  }
  setValid(t, e) {
    return this.length = Math.max(t + 1, this.length), e;
  }
}
class Cl extends nt {
  setValue(t, e) {
    const { children: i, type: s } = this;
    switch (Array.isArray(e) || e.constructor) {
      case !0:
        return s.children.forEach((r, o) => i[o].set(t, e[o]));
      case Map:
        return s.children.forEach((r, o) => i[o].set(t, e.get(r.name)));
      default:
        return s.children.forEach((r, o) => i[o].set(t, e[r.name]));
    }
  }
  /** @inheritdoc */
  setValid(t, e) {
    return super.setValid(t, e) || this.children.forEach((i) => i.setValid(t, e)), e;
  }
  addChild(t, e = `${this.numChildren}`) {
    const i = this.children.push(t);
    return this.type = new H([...this.type.children, new U(e, t.type, !0)]), i;
  }
}
class Me extends Vt {
}
Me.prototype._setValue = Tr;
class jo extends Me {
}
jo.prototype._setValue = Di;
class $o extends Me {
}
$o.prototype._setValue = Oi;
class Yo extends Me {
}
Yo.prototype._setValue = Fi;
class Wo extends Me {
}
Wo.prototype._setValue = Mi;
class Ne extends Vt {
}
Ne.prototype._setValue = Lr;
class Ho extends Ne {
}
Ho.prototype._setValue = Ni;
class Jo extends Ne {
}
Jo.prototype._setValue = Ti;
class Ko extends Ne {
}
Ko.prototype._setValue = Li;
class Go extends Ne {
}
Go.prototype._setValue = Ui;
class Zi extends nt {
  constructor(t) {
    super(t), this._typeIds = new Xe(Int8Array, 0, 1), typeof t.valueToChildTypeId == "function" && (this._valueToChildTypeId = t.valueToChildTypeId);
  }
  get typeIdToChildIndex() {
    return this.type.typeIdToChildIndex;
  }
  append(t, e) {
    return this.set(this.length, t, e);
  }
  set(t, e, i) {
    return i === void 0 && (i = this._valueToChildTypeId(this, e, t)), this.setValue(t, e, i), this;
  }
  setValue(t, e, i) {
    this._typeIds.set(t, i);
    const s = this.type.typeIdToChildIndex[i], r = this.children[s];
    r?.set(t, e), this.length = Math.max(t + 1, this.length);
  }
  addChild(t, e = `${this.children.length}`) {
    const i = this.children.push(t), { type: { children: s, mode: r, typeIds: o } } = this, a = [...s, new U(e, t.type)];
    return this.type = new Ke(r, [...o, i], a), i;
  }
  /** @ignore */
  // @ts-ignore
  _valueToChildTypeId(t, e, i) {
    throw new Error("Cannot map UnionBuilder value to child typeId. Pass the `childTypeId` as the second argument to unionBuilder.append(), or supply a `valueToChildTypeId` function as part of the UnionBuilder constructor options.");
  }
}
class El extends Zi {
}
class Vl extends Zi {
  constructor(t) {
    super(t), this._offsets = new Xe(Int32Array);
  }
  /** @ignore */
  setValue(t, e, i) {
    const s = this._typeIds.set(t, i).buffer[t], r = this.getChildAt(this.type.typeIdToChildIndex[s]), o = this._offsets.set(t, r.length).buffer[t];
    r?.set(o, e), this.length = Math.max(t + 1, this.length);
  }
}
class qo extends De {
  constructor(t) {
    super(t), this._values = new Qe(Uint8Array);
  }
  get byteLength() {
    let t = this._pendingLength + this.length * 4;
    return this._offsets && (t += this._offsets.byteLength), this._values && (t += this._values.byteLength), this._nulls && (t += this._nulls.byteLength), t;
  }
  setValue(t, e) {
    return super.setValue(t, Ze(e));
  }
  // @ts-ignore
  _flushPending(t, e) {
  }
}
qo.prototype._flushPending = Mo.prototype._flushPending;
class Zo extends De {
  constructor(t) {
    super(t), this._values = new Qe(Uint8Array);
  }
  get byteLength() {
    let t = this._pendingLength + this.length * 4;
    return this._offsets && (t += this._offsets.byteLength), this._values && (t += this._values.byteLength), this._nulls && (t += this._nulls.byteLength), t;
  }
  setValue(t, e) {
    return super.setValue(t, Ze(e));
  }
  // @ts-ignore
  _flushPending(t, e) {
  }
}
Zo.prototype._flushPending = No.prototype._flushPending;
class Rl extends M {
  visitNull() {
    return xl;
  }
  visitBool() {
    return bl;
  }
  visitInt() {
    return Rt;
  }
  visitInt8() {
    return Bl;
  }
  visitInt16() {
    return Al;
  }
  visitInt32() {
    return Dl;
  }
  visitInt64() {
    return Ol;
  }
  visitUint8() {
    return Fl;
  }
  visitUint16() {
    return Ml;
  }
  visitUint32() {
    return Nl;
  }
  visitUint64() {
    return Tl;
  }
  visitFloat() {
    return Hn;
  }
  visitFloat16() {
    return wl;
  }
  visitFloat32() {
    return Il;
  }
  visitFloat64() {
    return Sl;
  }
  visitUtf8() {
    return qo;
  }
  visitLargeUtf8() {
    return Zo;
  }
  visitBinary() {
    return Mo;
  }
  visitLargeBinary() {
    return No;
  }
  visitFixedSizeBinary() {
    return xo;
  }
  visitDate() {
    return Wn;
  }
  visitDateDay() {
    return To;
  }
  visitDateMillisecond() {
    return Lo;
  }
  visitTimestamp() {
    return Me;
  }
  visitTimestampSecond() {
    return jo;
  }
  visitTimestampMillisecond() {
    return $o;
  }
  visitTimestampMicrosecond() {
    return Yo;
  }
  visitTimestampNanosecond() {
    return Wo;
  }
  visitTime() {
    return Ne;
  }
  visitTimeSecond() {
    return Ho;
  }
  visitTimeMillisecond() {
    return Jo;
  }
  visitTimeMicrosecond() {
    return Ko;
  }
  visitTimeNanosecond() {
    return Go;
  }
  visitDecimal() {
    return Uo;
  }
  visitList() {
    return Ll;
  }
  visitStruct() {
    return Cl;
  }
  visitUnion() {
    return Zi;
  }
  visitDenseUnion() {
    return Vl;
  }
  visitSparseUnion() {
    return El;
  }
  visitDictionary() {
    return _l;
  }
  visitInterval() {
    return tn;
  }
  visitIntervalDayTime() {
    return Co;
  }
  visitIntervalYearMonth() {
    return Eo;
  }
  visitIntervalMonthDayNano() {
    return Vo;
  }
  visitDuration() {
    return Fe;
  }
  visitDurationSecond() {
    return Ro;
  }
  visitDurationMillisecond() {
    return zo;
  }
  visitDurationMicrosecond() {
    return ko;
  }
  visitDurationNanosecond() {
    return Po;
  }
  visitFixedSizeList() {
    return vl;
  }
  visitMap() {
    return Ul;
  }
}
const zl = new Rl();
class m extends M {
  compareSchemas(t, e) {
    return t === e || e instanceof t.constructor && this.compareManyFields(t.fields, e.fields);
  }
  compareManyFields(t, e) {
    return t === e || Array.isArray(t) && Array.isArray(e) && t.length === e.length && t.every((i, s) => this.compareFields(i, e[s]));
  }
  compareFields(t, e) {
    return t === e || e instanceof t.constructor && t.name === e.name && t.nullable === e.nullable && this.visit(t.type, e.type);
  }
}
function q(n, t) {
  return t instanceof n.constructor;
}
function ee(n, t) {
  return n === t || q(n, t);
}
function zt(n, t) {
  return n === t || q(n, t) && n.bitWidth === t.bitWidth && n.isSigned === t.isSigned;
}
function Jn(n, t) {
  return n === t || q(n, t) && n.precision === t.precision;
}
function kl(n, t) {
  return n === t || q(n, t) && n.byteWidth === t.byteWidth;
}
function Qi(n, t) {
  return n === t || q(n, t) && n.unit === t.unit;
}
function en(n, t) {
  return n === t || q(n, t) && n.unit === t.unit && n.timezone === t.timezone;
}
function nn(n, t) {
  return n === t || q(n, t) && n.unit === t.unit && n.bitWidth === t.bitWidth;
}
function Pl(n, t) {
  return n === t || q(n, t) && n.children.length === t.children.length && Ct.compareManyFields(n.children, t.children);
}
function jl(n, t) {
  return n === t || q(n, t) && n.children.length === t.children.length && Ct.compareManyFields(n.children, t.children);
}
function Xi(n, t) {
  return n === t || q(n, t) && n.mode === t.mode && n.typeIds.every((e, i) => e === t.typeIds[i]) && Ct.compareManyFields(n.children, t.children);
}
function $l(n, t) {
  return n === t || q(n, t) && n.id === t.id && n.isOrdered === t.isOrdered && Ct.visit(n.indices, t.indices) && Ct.visit(n.dictionary, t.dictionary);
}
function Kn(n, t) {
  return n === t || q(n, t) && n.unit === t.unit;
}
function sn(n, t) {
  return n === t || q(n, t) && n.unit === t.unit;
}
function Yl(n, t) {
  return n === t || q(n, t) && n.listSize === t.listSize && n.children.length === t.children.length && Ct.compareManyFields(n.children, t.children);
}
function Wl(n, t) {
  return n === t || q(n, t) && n.keysSorted === t.keysSorted && n.children.length === t.children.length && Ct.compareManyFields(n.children, t.children);
}
m.prototype.visitNull = ee;
m.prototype.visitBool = ee;
m.prototype.visitInt = zt;
m.prototype.visitInt8 = zt;
m.prototype.visitInt16 = zt;
m.prototype.visitInt32 = zt;
m.prototype.visitInt64 = zt;
m.prototype.visitUint8 = zt;
m.prototype.visitUint16 = zt;
m.prototype.visitUint32 = zt;
m.prototype.visitUint64 = zt;
m.prototype.visitFloat = Jn;
m.prototype.visitFloat16 = Jn;
m.prototype.visitFloat32 = Jn;
m.prototype.visitFloat64 = Jn;
m.prototype.visitUtf8 = ee;
m.prototype.visitLargeUtf8 = ee;
m.prototype.visitBinary = ee;
m.prototype.visitLargeBinary = ee;
m.prototype.visitFixedSizeBinary = kl;
m.prototype.visitDate = Qi;
m.prototype.visitDateDay = Qi;
m.prototype.visitDateMillisecond = Qi;
m.prototype.visitTimestamp = en;
m.prototype.visitTimestampSecond = en;
m.prototype.visitTimestampMillisecond = en;
m.prototype.visitTimestampMicrosecond = en;
m.prototype.visitTimestampNanosecond = en;
m.prototype.visitTime = nn;
m.prototype.visitTimeSecond = nn;
m.prototype.visitTimeMillisecond = nn;
m.prototype.visitTimeMicrosecond = nn;
m.prototype.visitTimeNanosecond = nn;
m.prototype.visitDecimal = ee;
m.prototype.visitList = Pl;
m.prototype.visitStruct = jl;
m.prototype.visitUnion = Xi;
m.prototype.visitDenseUnion = Xi;
m.prototype.visitSparseUnion = Xi;
m.prototype.visitDictionary = $l;
m.prototype.visitInterval = Kn;
m.prototype.visitIntervalDayTime = Kn;
m.prototype.visitIntervalYearMonth = Kn;
m.prototype.visitIntervalMonthDayNano = Kn;
m.prototype.visitDuration = sn;
m.prototype.visitDurationSecond = sn;
m.prototype.visitDurationMillisecond = sn;
m.prototype.visitDurationMicrosecond = sn;
m.prototype.visitDurationNanosecond = sn;
m.prototype.visitFixedSizeList = Yl;
m.prototype.visitMap = Wl;
const Ct = new m();
function Hl(n, t) {
  return Ct.compareSchemas(n, t);
}
function Jl(n, t) {
  return Ct.visit(n, t);
}
function xn(n) {
  const t = n.type, e = new (zl.getVisitFn(t)())(n);
  if (t.children && t.children.length > 0) {
    const i = n.children || [], s = { nullValues: n.nullValues }, r = Array.isArray(i) ? ((o, a) => i[a] || s) : (({ name: o }) => i[o] || s);
    for (const [o, a] of t.children.entries()) {
      const { type: c } = a, u = r(a, o);
      e.children.push(xn(Object.assign(Object.assign({}, u), { type: c })));
    }
  }
  return e;
}
function he(n, t) {
  if (n instanceof L || n instanceof A || n.type instanceof f || ArrayBuffer.isView(n))
    return _o(n);
  const e = { type: t ?? bn(n), nullValues: [null] }, i = [...Kl(e)(n)], s = i.length === 1 ? i[0] : i.reduce((r, o) => r.concat(o));
  return f.isDictionary(s.type) ? s.memoize() : s;
}
function bn(n) {
  if (n.length === 0)
    return new xt();
  let t = 0, e = 0, i = 0, s = 0, r = 0, o = 0, a = 0, c = 0;
  for (const u of n) {
    if (u == null) {
      ++t;
      continue;
    }
    switch (typeof u) {
      case "bigint":
        ++o;
        continue;
      case "boolean":
        ++a;
        continue;
      case "number":
        ++s;
        continue;
      case "string":
        ++r;
        continue;
      case "object":
        Array.isArray(u) ? ++e : Object.prototype.toString.call(u) === "[object Date]" ? ++c : ++i;
        continue;
    }
    throw new TypeError("Unable to infer Vector type from input values, explicit type declaration expected.");
  }
  if (s + t === n.length)
    return new jn();
  if (r + t === n.length)
    return new Wt(new We(), new Yt());
  if (o + t === n.length)
    return new Ii();
  if (a + t === n.length)
    return new He();
  if (c + t === n.length)
    return new Ta();
  if (e + t === n.length) {
    const u = n, d = bn(u[u.findIndex((h) => h != null)]);
    if (u.every((h) => h == null || Jl(d, bn(h))))
      return new we(new U("", d, !0));
  } else if (i + t === n.length) {
    const u = /* @__PURE__ */ new Map();
    for (const d of n)
      for (const h of Object.keys(d))
        !u.has(h) && d[h] != null && u.set(h, new U(h, bn([d[h]]), !0));
    return new H([...u.values()]);
  }
  throw new TypeError("Unable to infer Vector type from input values, explicit type declaration expected.");
}
function Kl(n) {
  const { ["queueingStrategy"]: t = "count" } = n, { ["highWaterMark"]: e = t !== "bytes" ? Number.POSITIVE_INFINITY : Math.pow(2, 14) } = n, i = t !== "bytes" ? "length" : "byteLength";
  return function* (s) {
    let r = 0;
    const o = xn(n);
    for (const a of s)
      o.append(a)[i] >= e && ++r && (yield o.toVector());
    (o.finish().length > 0 || r === 0) && (yield o.toVector());
  };
}
function ii(n, t) {
  return Gl(n, t.map((e) => e.data.concat()));
}
function Gl(n, t) {
  const e = [...n.fields], i = [], s = { numBatches: t.reduce((h, T) => Math.max(h, T.length), 0) };
  let r = 0, o = 0, a = -1;
  const c = t.length;
  let u, d = [];
  for (; s.numBatches-- > 0; ) {
    for (o = Number.POSITIVE_INFINITY, a = -1; ++a < c; )
      d[a] = u = t[a].shift(), o = Math.min(o, u ? u.length : o);
    Number.isFinite(o) && (d = ql(e, o, d, t, s), o > 0 && (i[r++] = I({
      type: new H(e),
      length: o,
      nullCount: 0,
      children: d.slice()
    })));
  }
  return [
    n = n.assign(e),
    i.map((h) => new K(n, h))
  ];
}
function ql(n, t, e, i, s) {
  var r;
  const o = (t + 63 & -64) >> 3;
  for (let a = -1, c = i.length; ++a < c; ) {
    const u = e[a], d = u?.length;
    if (d >= t)
      d === t ? e[a] = u : (e[a] = u.slice(0, t), s.numBatches = Math.max(s.numBatches, i[a].unshift(u.slice(t, d - t))));
    else {
      const h = n[a];
      n[a] = h.clone({ nullable: !0 }), e[a] = (r = u?._changeLengthAndBackfillNullBitmap(t)) !== null && r !== void 0 ? r : I({
        type: h.type,
        length: t,
        nullCount: t,
        nullBitmap: new Uint8Array(o)
      });
    }
  }
  return e;
}
var Qo;
class rt {
  constructor(...t) {
    var e, i;
    if (t.length === 0)
      return this.batches = [], this.schema = new C([]), this._offsets = [0], this;
    let s, r;
    t[0] instanceof C && (s = t.shift()), t.at(-1) instanceof Uint32Array && (r = t.pop());
    const o = (c) => {
      if (c) {
        if (c instanceof K)
          return [c];
        if (c instanceof rt)
          return c.batches;
        if (c instanceof L) {
          if (c.type instanceof H)
            return [new K(new C(c.type.children), c)];
        } else {
          if (Array.isArray(c))
            return c.flatMap((u) => o(u));
          if (typeof c[Symbol.iterator] == "function")
            return [...c].flatMap((u) => o(u));
          if (typeof c == "object") {
            const u = Object.keys(c), d = u.map((O) => new A([c[O]])), h = s ?? new C(u.map((O, j) => new U(String(O), d[j].type, d[j].nullable))), [, T] = ii(h, d);
            return T.length === 0 ? [new K(c)] : T;
          }
        }
      }
      return [];
    }, a = t.flatMap((c) => o(c));
    if (s = (i = s ?? ((e = a[0]) === null || e === void 0 ? void 0 : e.schema)) !== null && i !== void 0 ? i : new C([]), !(s instanceof C))
      throw new TypeError("Table constructor expects a [Schema, RecordBatch[]] pair.");
    for (const c of a) {
      if (!(c instanceof K))
        throw new TypeError("Table constructor expects a [Schema, RecordBatch[]] pair.");
      if (!Hl(s, c.schema))
        throw new TypeError("Table and inner RecordBatch schemas must be equivalent.");
    }
    this.schema = s, this.batches = a, this._offsets = r ?? uo(this.data);
  }
  /**
   * The contiguous {@link RecordBatch `RecordBatch`} chunks of the Table rows.
   */
  get data() {
    return this.batches.map(({ data: t }) => t);
  }
  /**
   * The number of columns in this Table.
   */
  get numCols() {
    return this.schema.fields.length;
  }
  /**
   * The number of rows in this Table.
   */
  get numRows() {
    return this.data.reduce((t, e) => t + e.length, 0);
  }
  /**
   * The number of null rows in this Table.
   */
  get nullCount() {
    return this._nullCount === -1 && (this._nullCount = lo(this.data)), this._nullCount;
  }
  /**
   * Check whether an element is null.
   *
   * @param index The index at which to read the validity bitmap.
   */
  // @ts-ignore
  isValid(t) {
    return !1;
  }
  /**
   * Get an element value by position.
   *
   * @param index The index of the element to read.
   */
  // @ts-ignore
  get(t) {
    return null;
  }
  /**
    * Get an element value by position.
    * @param index The index of the element to read. A negative index will count back from the last element.
    */
  // @ts-ignore
  at(t) {
    return this.get(ji(t, this.numRows));
  }
  /**
   * Set an element value by position.
   *
   * @param index The index of the element to write.
   * @param value The value to set.
   */
  // @ts-ignore
  set(t, e) {
  }
  /**
   * Retrieve the index of the first occurrence of a value in an Vector.
   *
   * @param element The value to locate in the Vector.
   * @param offset The index at which to begin the search. If offset is omitted, the search starts at index 0.
   */
  // @ts-ignore
  indexOf(t, e) {
    return -1;
  }
  /**
   * Iterator for rows in this Table.
   */
  [Symbol.iterator]() {
    return this.batches.length > 0 ? Hi.visit(new A(this.data)) : new Array(0)[Symbol.iterator]();
  }
  /**
   * Return a JavaScript Array of the Table rows.
   *
   * @returns An Array of Table rows.
   */
  toArray() {
    return [...this];
  }
  /**
   * Returns a string representation of the Table rows.
   *
   * @returns A string representation of the Table rows.
   */
  toString() {
    return `[
  ${this.toArray().join(`,
  `)}
]`;
  }
  /**
   * Combines two or more Tables of the same schema.
   *
   * @param others Additional Tables to add to the end of this Tables.
   */
  concat(...t) {
    const e = this.schema, i = this.data.concat(t.flatMap(({ data: s }) => s));
    return new rt(e, i.map((s) => new K(e, s)));
  }
  /**
   * Return a zero-copy sub-section of this Table.
   *
   * @param begin The beginning of the specified portion of the Table.
   * @param end The end of the specified portion of the Table. This is exclusive of the element at the index 'end'.
   */
  slice(t, e) {
    const i = this.schema;
    [t, e] = ao({ length: this.numRows }, t, e);
    const s = ho(this.data, this._offsets, t, e);
    return new rt(i, s.map((r) => new K(i, r)));
  }
  /**
   * Returns a child Vector by name, or null if this Vector has no child with the given name.
   *
   * @param name The name of the child to retrieve.
   */
  getChild(t) {
    return this.getChildAt(this.schema.fields.findIndex((e) => e.name === t));
  }
  /**
   * Returns a child Vector by index, or null if this Vector has no child at the supplied index.
   *
   * @param index The index of the child to retrieve.
   */
  getChildAt(t) {
    if (t > -1 && t < this.schema.fields.length) {
      const e = this.data.map((i) => i.children[t]);
      if (e.length === 0) {
        const { type: i } = this.schema.fields[t], s = I({ type: i, length: 0, nullCount: 0 });
        e.push(s._changeLengthAndBackfillNullBitmap(this.numRows));
      }
      return new A(e);
    }
    return null;
  }
  /**
   * Sets a child Vector by name.
   *
   * @param name The name of the child to overwrite.
   * @returns A new Table with the supplied child for the specified name.
   */
  setChild(t, e) {
    var i;
    return this.setChildAt((i = this.schema.fields) === null || i === void 0 ? void 0 : i.findIndex((s) => s.name === t), e);
  }
  setChildAt(t, e) {
    let i = this.schema, s = [...this.batches];
    if (t > -1 && t < this.numCols) {
      e || (e = new A([I({ type: new xt(), length: this.numRows })]));
      const r = i.fields.slice(), o = r[t].clone({ type: e.type }), a = this.schema.fields.map((c, u) => this.getChildAt(u));
      [r[t], a[t]] = [o, e], [i, s] = ii(i, a);
    }
    return new rt(i, s);
  }
  /**
   * Construct a new Table containing only specified columns.
   *
   * @param columnNames Names of columns to keep.
   * @returns A new Table of columns matching the specified names.
   */
  select(t) {
    const e = this.schema.fields.reduce((i, s, r) => i.set(s.name, r), /* @__PURE__ */ new Map());
    return this.selectAt(t.map((i) => e.get(i)).filter((i) => i > -1));
  }
  /**
   * Construct a new Table containing only columns at the specified indices.
   *
   * @param columnIndices Indices of columns to keep.
   * @returns A new Table of columns at the specified indices.
   */
  selectAt(t) {
    const e = this.schema.selectAt(t), i = this.batches.map((s) => s.selectAt(t));
    return new rt(e, i);
  }
  assign(t) {
    const e = this.schema.fields, [i, s] = t.schema.fields.reduce((a, c, u) => {
      const [d, h] = a, T = e.findIndex((O) => O.name === c.name);
      return ~T ? h[T] = u : d.push(u), a;
    }, [[], []]), r = this.schema.assign(t.schema), o = [
      ...e.map((a, c) => [c, s[c]]).map(([a, c]) => c === void 0 ? this.getChildAt(a) : t.getChildAt(c)),
      ...i.map((a) => t.getChildAt(a))
    ].filter(Boolean);
    return new rt(...ii(r, o));
  }
}
Qo = Symbol.toStringTag;
rt[Qo] = ((n) => (n.schema = null, n.batches = [], n._offsets = new Uint32Array([0]), n._nullCount = -1, n[Symbol.isConcatSpreadable] = !0, n.isValid = Mn(Wi), n.get = Mn(et.getVisitFn(l.Struct)), n.set = fo(ht.getVisitFn(l.Struct)), n.indexOf = po(Nn.getVisitFn(l.Struct)), "Table"))(rt.prototype);
function Zl(n) {
  const t = {}, e = Object.entries(n);
  for (const [i, s] of e)
    t[i] = he(s);
  return new rt(t);
}
var Xo;
class K {
  constructor(...t) {
    switch (t.length) {
      case 2: {
        if ([this.schema] = t, !(this.schema instanceof C))
          throw new TypeError("RecordBatch constructor expects a [Schema, Data] pair.");
        if ([
          ,
          this.data = I({
            nullCount: 0,
            type: new H(this.schema.fields),
            children: this.schema.fields.map((e) => I({ type: e.type, nullCount: 0 }))
          })
        ] = t, !(this.data instanceof L))
          throw new TypeError("RecordBatch constructor expects a [Schema, Data] pair.");
        [this.schema, this.data] = Ns(this.schema, this.data.children);
        break;
      }
      case 1: {
        const [e] = t, { fields: i, children: s, length: r } = Object.keys(e).reduce((c, u, d) => (c.children[d] = e[u], c.length = Math.max(c.length, e[u].length), c.fields[d] = U.new({ name: u, type: e[u].type, nullable: !0 }), c), {
          length: 0,
          fields: new Array(),
          children: new Array()
        }), o = new C(i), a = I({ type: new H(i), length: r, children: s, nullCount: 0 });
        [this.schema, this.data] = Ns(o, a.children, r);
        break;
      }
      default:
        throw new TypeError("RecordBatch constructor expects an Object mapping names to child Data, or a [Schema, Data] pair.");
    }
  }
  get dictionaries() {
    return this._dictionaries || (this._dictionaries = ta(this.schema.fields, this.data.children));
  }
  /**
   * The number of columns in this RecordBatch.
   */
  get numCols() {
    return this.schema.fields.length;
  }
  /**
   * The number of rows in this RecordBatch.
   */
  get numRows() {
    return this.data.length;
  }
  /**
   * The number of null rows in this RecordBatch.
   */
  get nullCount() {
    return this.data.nullCount;
  }
  /**
   * Check whether an row is null.
   * @param index The index at which to read the validity bitmap.
   */
  isValid(t) {
    return this.data.getValid(t);
  }
  /**
   * Get a row by position.
   * @param index The index of the row to read.
   */
  get(t) {
    return et.visit(this.data, t);
  }
  /**
    * Get a row value by position.
    * @param index The index of the row to read. A negative index will count back from the last row.
    */
  at(t) {
    return this.get(ji(t, this.numRows));
  }
  /**
   * Set a row by position.
   * @param index The index of the row to write.
   * @param value The value to set.
   */
  set(t, e) {
    return ht.visit(this.data, t, e);
  }
  /**
   * Retrieve the index of the first occurrence of a row in an RecordBatch.
   * @param element The row to locate in the RecordBatch.
   * @param offset The index at which to begin the search. If offset is omitted, the search starts at index 0.
   */
  indexOf(t, e) {
    return Nn.visit(this.data, t, e);
  }
  /**
   * Iterator for rows in this RecordBatch.
   */
  [Symbol.iterator]() {
    return Hi.visit(new A([this.data]));
  }
  /**
   * Return a JavaScript Array of the RecordBatch rows.
   * @returns An Array of RecordBatch rows.
   */
  toArray() {
    return [...this];
  }
  /**
   * Combines two or more RecordBatch of the same schema.
   * @param others Additional RecordBatch to add to the end of this RecordBatch.
   */
  concat(...t) {
    return new rt(this.schema, [this, ...t]);
  }
  /**
   * Return a zero-copy sub-section of this RecordBatch.
   * @param start The beginning of the specified portion of the RecordBatch.
   * @param end The end of the specified portion of the RecordBatch. This is exclusive of the row at the index 'end'.
   */
  slice(t, e) {
    const [i] = new A([this.data]).slice(t, e).data;
    return new K(this.schema, i);
  }
  /**
   * Returns a child Vector by name, or null if this Vector has no child with the given name.
   * @param name The name of the child to retrieve.
   */
  getChild(t) {
    var e;
    return this.getChildAt((e = this.schema.fields) === null || e === void 0 ? void 0 : e.findIndex((i) => i.name === t));
  }
  /**
   * Returns a child Vector by index, or null if this Vector has no child at the supplied index.
   * @param index The index of the child to retrieve.
   */
  getChildAt(t) {
    return t > -1 && t < this.schema.fields.length ? new A([this.data.children[t]]) : null;
  }
  /**
   * Sets a child Vector by name.
   * @param name The name of the child to overwrite.
   * @returns A new RecordBatch with the new child for the specified name.
   */
  setChild(t, e) {
    var i;
    return this.setChildAt((i = this.schema.fields) === null || i === void 0 ? void 0 : i.findIndex((s) => s.name === t), e);
  }
  setChildAt(t, e) {
    let i = this.schema, s = this.data;
    if (t > -1 && t < this.numCols) {
      e || (e = new A([I({ type: new xt(), length: this.numRows })]));
      const r = i.fields.slice(), o = s.children.slice(), a = r[t].clone({ type: e.type });
      [r[t], o[t]] = [a, e.data[0]], i = new C(r, new Map(this.schema.metadata)), s = I({ type: new H(r), children: o });
    }
    return new K(i, s);
  }
  /**
   * Construct a new RecordBatch containing only specified columns.
   *
   * @param columnNames Names of columns to keep.
   * @returns A new RecordBatch of columns matching the specified names.
   */
  select(t) {
    const e = this.schema.select(t), i = new H(e.fields), s = [];
    for (const r of t) {
      const o = this.schema.fields.findIndex((a) => a.name === r);
      ~o && (s[o] = this.data.children[o]);
    }
    return new K(e, I({ type: i, length: this.numRows, children: s }));
  }
  /**
   * Construct a new RecordBatch containing only columns at the specified indices.
   *
   * @param columnIndices Indices of columns to keep.
   * @returns A new RecordBatch of columns matching at the specified indices.
   */
  selectAt(t) {
    const e = this.schema.selectAt(t), i = t.map((r) => this.data.children[r]).filter(Boolean), s = I({ type: new H(e.fields), length: this.numRows, children: i });
    return new K(e, s);
  }
}
Xo = Symbol.toStringTag;
K[Xo] = ((n) => (n._nullCount = -1, n[Symbol.isConcatSpreadable] = !0, "RecordBatch"))(K.prototype);
function Ns(n, t, e = t.reduce((i, s) => Math.max(i, s.length), 0)) {
  var i;
  const s = [...n.fields], r = [...t], o = (e + 63 & -64) >> 3;
  for (const [a, c] of n.fields.entries()) {
    const u = t[a];
    (!u || u.length !== e) && (s[a] = c.clone({ nullable: !0 }), r[a] = (i = u?._changeLengthAndBackfillNullBitmap(e)) !== null && i !== void 0 ? i : I({
      type: c.type,
      length: e,
      nullCount: e,
      nullBitmap: new Uint8Array(o)
    }));
  }
  return [
    n.assign(s),
    I({ type: new H(s), length: e, children: r })
  ];
}
function ta(n, t, e = /* @__PURE__ */ new Map()) {
  var i, s;
  if (((i = n?.length) !== null && i !== void 0 ? i : 0) > 0 && n?.length === t?.length)
    for (let r = -1, o = n.length; ++r < o; ) {
      const { type: a } = n[r], c = t[r];
      for (const u of [c, ...((s = c?.dictionary) === null || s === void 0 ? void 0 : s.data) || []])
        ta(a.children, u?.children, e);
      if (f.isDictionary(a)) {
        const { id: u } = a;
        if (!e.has(u))
          c?.dictionary && e.set(u, c.dictionary);
        else if (e.get(u) !== c.dictionary)
          throw new Error("Cannot create Schema containing two different dictionaries with the same Id");
      }
    }
  return e;
}
class ea extends K {
  constructor(t) {
    const e = t.fields.map((s) => I({ type: s.type })), i = I({ type: new H(t.fields), nullCount: 0, children: e });
    super(t, i);
  }
}
const ts = (n) => `Expected ${x[n]} Message in stream, but was null or length 0.`, es = (n) => `Header pointer of flatbuffer-encoded ${x[n]} Message is null or length 0.`, na = (n, t) => `Expected to read ${n} metadata bytes, but only read ${t}.`, ia = (n, t) => `Expected to read ${n} bytes for message body, but only read ${t}.`;
class sa {
  constructor(t) {
    this.source = t instanceof Ln ? t : new Ln(t);
  }
  [Symbol.iterator]() {
    return this;
  }
  next() {
    let t;
    return (t = this.readMetadataLength()).done || t.value === -1 && (t = this.readMetadataLength()).done || (t = this.readMetadata(t.value)).done ? P : t;
  }
  throw(t) {
    return this.source.throw(t);
  }
  return(t) {
    return this.source.return(t);
  }
  readMessage(t) {
    let e;
    if ((e = this.next()).done)
      return null;
    if (t != null && e.value.headerType !== t)
      throw new Error(ts(t));
    return e.value;
  }
  readMessageBody(t) {
    if (t <= 0)
      return new Uint8Array(0);
    const e = N(this.source.read(t));
    if (e.byteLength < t)
      throw new Error(ia(t, e.byteLength));
    return (
      /* 1. */
      e.byteOffset % 8 === 0 && /* 2. */
      e.byteOffset + e.byteLength <= e.buffer.byteLength ? e : e.slice()
    );
  }
  readSchema(t = !1) {
    const e = x.Schema, i = this.readMessage(e), s = i?.header();
    if (t && !s)
      throw new Error(es(e));
    return s;
  }
  readMetadataLength() {
    const t = this.source.read(Gn), e = t && new Qt(t), i = e?.readInt32(0) || 0;
    return { done: i === 0, value: i };
  }
  readMetadata(t) {
    const e = this.source.read(t);
    if (!e)
      return P;
    if (e.byteLength < t)
      throw new Error(na(t, e.byteLength));
    return { done: !1, value: pt.decode(e) };
  }
}
class Ql {
  constructor(t, e) {
    this.source = t instanceof Se ? t : Cs(t) ? new Un(t, e) : new Se(t);
  }
  [Symbol.asyncIterator]() {
    return this;
  }
  next() {
    return D(this, void 0, void 0, function* () {
      let t;
      return (t = yield this.readMetadataLength()).done || t.value === -1 && (t = yield this.readMetadataLength()).done || (t = yield this.readMetadata(t.value)).done ? P : t;
    });
  }
  throw(t) {
    return D(this, void 0, void 0, function* () {
      return yield this.source.throw(t);
    });
  }
  return(t) {
    return D(this, void 0, void 0, function* () {
      return yield this.source.return(t);
    });
  }
  readMessage(t) {
    return D(this, void 0, void 0, function* () {
      let e;
      if ((e = yield this.next()).done)
        return null;
      if (t != null && e.value.headerType !== t)
        throw new Error(ts(t));
      return e.value;
    });
  }
  readMessageBody(t) {
    return D(this, void 0, void 0, function* () {
      if (t <= 0)
        return new Uint8Array(0);
      const e = N(yield this.source.read(t));
      if (e.byteLength < t)
        throw new Error(ia(t, e.byteLength));
      return (
        /* 1. */
        e.byteOffset % 8 === 0 && /* 2. */
        e.byteOffset + e.byteLength <= e.buffer.byteLength ? e : e.slice()
      );
    });
  }
  readSchema() {
    return D(this, arguments, void 0, function* (t = !1) {
      const e = x.Schema, i = yield this.readMessage(e), s = i?.header();
      if (t && !s)
        throw new Error(es(e));
      return s;
    });
  }
  readMetadataLength() {
    return D(this, void 0, void 0, function* () {
      const t = yield this.source.read(Gn), e = t && new Qt(t), i = e?.readInt32(0) || 0;
      return { done: i === 0, value: i };
    });
  }
  readMetadata(t) {
    return D(this, void 0, void 0, function* () {
      const e = yield this.source.read(t);
      if (!e)
        return P;
      if (e.byteLength < t)
        throw new Error(na(t, e.byteLength));
      return { done: !1, value: pt.decode(e) };
    });
  }
}
class Xl extends sa {
  constructor(t) {
    super(new Uint8Array(0)), this._schema = !1, this._body = [], this._batchIndex = 0, this._dictionaryIndex = 0, this._json = t instanceof Fs ? t : new Fs(t);
  }
  next() {
    const { _json: t } = this;
    if (!this._schema)
      return this._schema = !0, { done: !1, value: pt.fromJSON(t.schema, x.Schema) };
    if (this._dictionaryIndex < t.dictionaries.length) {
      const e = t.dictionaries[this._dictionaryIndex++];
      return this._body = e.data.columns, { done: !1, value: pt.fromJSON(e, x.DictionaryBatch) };
    }
    if (this._batchIndex < t.batches.length) {
      const e = t.batches[this._batchIndex++];
      return this._body = e.columns, { done: !1, value: pt.fromJSON(e, x.RecordBatch) };
    }
    return this._body = [], P;
  }
  readMessageBody(t) {
    return e(this._body);
    function e(i) {
      return (i || []).reduce((s, r) => [
        ...s,
        ...r.VALIDITY && [r.VALIDITY] || [],
        ...r.TYPE_ID && [r.TYPE_ID] || [],
        ...r.OFFSET && [r.OFFSET] || [],
        ...r.DATA && [r.DATA] || [],
        ...e(r.children)
      ], []);
    }
  }
  readMessage(t) {
    let e;
    if ((e = this.next()).done)
      return null;
    if (t != null && e.value.headerType !== t)
      throw new Error(ts(t));
    return e.value;
  }
  readSchema() {
    const t = x.Schema, e = this.readMessage(t), i = e?.header();
    if (!e || !i)
      throw new Error(es(t));
    return i;
  }
}
const Gn = 4, mi = "ARROW1", Cn = new Uint8Array(mi.length);
for (let n = 0; n < mi.length; n += 1)
  Cn[n] = mi.codePointAt(n);
function ns(n, t = 0) {
  for (let e = -1, i = Cn.length; ++e < i; )
    if (Cn[e] !== n[t + e])
      return !1;
  return !0;
}
const rn = Cn.length, ra = rn + Gn, tu = rn * 2 + Gn;
class eu {
  constructor() {
    this.LZ4_FRAME_MAGIC = new Uint8Array([4, 34, 77, 24]), this.MIN_HEADER_LENGTH = 7;
  }
  isValidCodecEncode(t) {
    const e = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]), i = t.encode(e);
    return this._isValidCompressed(i);
  }
  _isValidCompressed(t) {
    return this._hasMinimumLength(t) && this._hasValidMagicNumber(t) && this._hasValidVersion(t);
  }
  _hasMinimumLength(t) {
    return t.length >= this.MIN_HEADER_LENGTH;
  }
  _hasValidMagicNumber(t) {
    return this.LZ4_FRAME_MAGIC.every((e, i) => t[i] === e);
  }
  _hasValidVersion(t) {
    return (t[4] & 192) >> 6 === 1;
  }
}
class nu {
  constructor() {
    this.ZSTD_MAGIC = new Uint8Array([40, 181, 47, 253]), this.MIN_HEADER_LENGTH = 6;
  }
  isValidCodecEncode(t) {
    const e = new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8]), i = t.encode(e);
    return this._isValidCompressed(i);
  }
  _isValidCompressed(t) {
    return this._hasMinimumLength(t) && this._hasValidMagicNumber(t);
  }
  _hasMinimumLength(t) {
    return t.length >= this.MIN_HEADER_LENGTH;
  }
  _hasValidMagicNumber(t) {
    return this.ZSTD_MAGIC.every((e, i) => t[i] === e);
  }
}
const iu = {
  [Xt.LZ4_FRAME]: new eu(),
  [Xt.ZSTD]: new nu()
};
class su {
  constructor() {
    this.registry = {};
  }
  set(t, e) {
    if (e?.encode && typeof e.encode == "function" && !iu[t].isValidCodecEncode(e))
      throw new Error(`Encoder for ${Xt[t]} is not valid.`);
    this.registry[t] = e;
  }
  get(t) {
    var e;
    return ((e = this.registry) === null || e === void 0 ? void 0 : e[t]) || null;
  }
}
const Ts = new su(), ru = -1, ou = 8;
class Ut extends Oo {
  constructor(t) {
    super(), this._impl = t;
  }
  get closed() {
    return this._impl.closed;
  }
  get schema() {
    return this._impl.schema;
  }
  get autoDestroy() {
    return this._impl.autoDestroy;
  }
  get dictionaries() {
    return this._impl.dictionaries;
  }
  get numDictionaries() {
    return this._impl.numDictionaries;
  }
  get numRecordBatches() {
    return this._impl.numRecordBatches;
  }
  get footer() {
    return this._impl.isFile() ? this._impl.footer : null;
  }
  isSync() {
    return this._impl.isSync();
  }
  isAsync() {
    return this._impl.isAsync();
  }
  isFile() {
    return this._impl.isFile();
  }
  isStream() {
    return this._impl.isStream();
  }
  next() {
    return this._impl.next();
  }
  throw(t) {
    return this._impl.throw(t);
  }
  return(t) {
    return this._impl.return(t);
  }
  cancel() {
    return this._impl.cancel();
  }
  reset(t) {
    return this._impl.reset(t), this._DOMStream = void 0, this._nodeStream = void 0, this;
  }
  open(t) {
    const e = this._impl.open(t);
    return Pe(e) ? e.then(() => this) : this;
  }
  readRecordBatch(t) {
    return this._impl.isFile() ? this._impl.readRecordBatch(t) : null;
  }
  [Symbol.iterator]() {
    return this._impl[Symbol.iterator]();
  }
  [Symbol.asyncIterator]() {
    return this._impl[Symbol.asyncIterator]();
  }
  toDOMStream() {
    return ct.toDOMStream(this.isSync() ? { [Symbol.iterator]: () => this } : { [Symbol.asyncIterator]: () => this });
  }
  toNodeStream() {
    return ct.toNodeStream(this.isSync() ? { [Symbol.iterator]: () => this } : { [Symbol.asyncIterator]: () => this }, { objectMode: !0 });
  }
  /** @nocollapse */
  // @ts-ignore
  static throughNode(t) {
    throw new Error('"throughNode" not available in this environment');
  }
  /** @nocollapse */
  static throughDOM(t, e) {
    throw new Error('"throughDOM" not available in this environment');
  }
  /** @nocollapse */
  static from(t) {
    return t instanceof Ut ? t : oi(t) ? uu(t) : Cs(t) ? fu(t) : Pe(t) ? D(this, void 0, void 0, function* () {
      return yield Ut.from(yield t);
    }) : Es(t) || _i(t) || Rs(t) || bi(t) ? hu(new Se(t)) : du(new Ln(t));
  }
  /** @nocollapse */
  static readAll(t) {
    return t instanceof Ut ? t.isSync() ? Ls(t) : Us(t) : oi(t) || ArrayBuffer.isView(t) || Pn(t) || xs(t) ? Ls(t) : Us(t);
  }
}
class En extends Ut {
  constructor(t) {
    super(t), this._impl = t;
  }
  readAll() {
    return [...this];
  }
  [Symbol.iterator]() {
    return this._impl[Symbol.iterator]();
  }
  [Symbol.asyncIterator]() {
    return Dt(this, arguments, function* () {
      yield F(yield* ln(pe(this[Symbol.iterator]())));
    });
  }
}
class Vn extends Ut {
  constructor(t) {
    super(t), this._impl = t;
  }
  readAll() {
    return D(this, void 0, void 0, function* () {
      var t, e, i, s;
      const r = new Array();
      try {
        for (var o = !0, a = pe(this), c; c = yield a.next(), t = c.done, !t; o = !0) {
          s = c.value, o = !1;
          const u = s;
          r.push(u);
        }
      } catch (u) {
        e = { error: u };
      } finally {
        try {
          !o && !t && (i = a.return) && (yield i.call(a));
        } finally {
          if (e) throw e.error;
        }
      }
      return r;
    });
  }
  [Symbol.iterator]() {
    throw new Error("AsyncRecordBatchStreamReader is not Iterable");
  }
  [Symbol.asyncIterator]() {
    return this._impl[Symbol.asyncIterator]();
  }
}
class oa extends En {
  constructor(t) {
    super(t), this._impl = t;
  }
}
class au extends Vn {
  constructor(t) {
    super(t), this._impl = t;
  }
}
class aa {
  get numDictionaries() {
    return this._dictionaryIndex;
  }
  get numRecordBatches() {
    return this._recordBatchIndex;
  }
  constructor(t = /* @__PURE__ */ new Map()) {
    this.closed = !1, this.autoDestroy = !0, this._dictionaryIndex = 0, this._recordBatchIndex = 0, this.dictionaries = t;
  }
  isSync() {
    return !1;
  }
  isAsync() {
    return !1;
  }
  isFile() {
    return !1;
  }
  isStream() {
    return !1;
  }
  reset(t) {
    return this._dictionaryIndex = 0, this._recordBatchIndex = 0, this.schema = t, this.dictionaries = /* @__PURE__ */ new Map(), this;
  }
  _loadRecordBatch(t, e) {
    let i;
    if (t.compression != null) {
      const r = Ts.get(t.compression.type);
      if (r?.decode && typeof r.decode == "function") {
        const { decommpressedBody: o, buffers: a } = this._decompressBuffers(t, e, r);
        i = this._loadCompressedVectors(t, o, this.schema.fields), t = new ot(t.length, t.nodes, a, null);
      } else
        throw new Error("Record batch is compressed but codec not found");
    } else
      i = this._loadVectors(t, e, this.schema.fields);
    const s = I({ type: new H(this.schema.fields), length: t.length, children: i });
    return new K(this.schema, s);
  }
  _loadDictionaryBatch(t, e) {
    const { id: i, isDelta: s } = t, { dictionaries: r, schema: o } = this, a = r.get(i), c = o.dictionaries.get(i);
    let u;
    if (t.data.compression != null) {
      const d = Ts.get(t.data.compression.type);
      if (d?.decode && typeof d.decode == "function") {
        const { decommpressedBody: h, buffers: T } = this._decompressBuffers(t.data, e, d);
        u = this._loadCompressedVectors(t.data, h, [c]), t = new Ft(new ot(t.data.length, t.data.nodes, T, null), i, s);
      } else
        throw new Error("Dictionary batch is compressed but codec not found");
    } else
      u = this._loadVectors(t.data, e, [c]);
    return (a && s ? a.concat(new A(u)) : new A(u)).memoize();
  }
  _loadVectors(t, e, i) {
    return new qi(e, t.nodes, t.buffers, this.dictionaries, this.schema.metadataVersion).visitMany(i);
  }
  _loadCompressedVectors(t, e, i) {
    return new ml(e, t.nodes, t.buffers, this.dictionaries, this.schema.metadataVersion).visitMany(i);
  }
  _decompressBuffers(t, e, i) {
    const s = [], r = [];
    let o = 0;
    for (const { offset: a, length: c } of t.buffers) {
      if (c === 0) {
        s.push(new Uint8Array(0)), r.push(new yt(o, 0));
        continue;
      }
      const u = new Qt(e.subarray(a, a + c)), d = k(u.readInt64(0)), h = u.bytes().subarray(ou), T = d === ru ? h : i.decode(h);
      s.push(T);
      const O = (o + 7 & -8) - o;
      o += O, r.push(new yt(o, T.length)), o += T.length;
    }
    return {
      decommpressedBody: s,
      buffers: r
    };
  }
}
class Rn extends aa {
  constructor(t, e) {
    super(e), this._reader = oi(t) ? new Xl(this._handle = t) : new sa(this._handle = t);
  }
  isSync() {
    return !0;
  }
  isStream() {
    return !0;
  }
  [Symbol.iterator]() {
    return this;
  }
  cancel() {
    !this.closed && (this.closed = !0) && (this.reset()._reader.return(), this._reader = null, this.dictionaries = null);
  }
  open(t) {
    return this.closed || (this.autoDestroy = la(this, t), this.schema || (this.schema = this._reader.readSchema()) || this.cancel()), this;
  }
  throw(t) {
    return !this.closed && this.autoDestroy && (this.closed = !0) ? this.reset()._reader.throw(t) : P;
  }
  return(t) {
    return !this.closed && this.autoDestroy && (this.closed = !0) ? this.reset()._reader.return(t) : P;
  }
  next() {
    if (this.closed)
      return P;
    let t;
    const { _reader: e } = this;
    for (; t = this._readNextMessageAndValidate(); )
      if (t.isSchema())
        this.reset(t.header());
      else if (t.isRecordBatch()) {
        this._recordBatchIndex++;
        const i = t.header(), s = e.readMessageBody(t.bodyLength);
        return { done: !1, value: this._loadRecordBatch(i, s) };
      } else if (t.isDictionaryBatch()) {
        this._dictionaryIndex++;
        const i = t.header(), s = e.readMessageBody(t.bodyLength), r = this._loadDictionaryBatch(i, s);
        this.dictionaries.set(i.id, r);
      }
    return this.schema && this._recordBatchIndex === 0 ? (this._recordBatchIndex++, { done: !1, value: new ea(this.schema) }) : this.return();
  }
  _readNextMessageAndValidate(t) {
    return this._reader.readMessage(t);
  }
}
class zn extends aa {
  constructor(t, e) {
    super(e), this._reader = new Ql(this._handle = t);
  }
  isAsync() {
    return !0;
  }
  isStream() {
    return !0;
  }
  [Symbol.asyncIterator]() {
    return this;
  }
  cancel() {
    return D(this, void 0, void 0, function* () {
      !this.closed && (this.closed = !0) && (yield this.reset()._reader.return(), this._reader = null, this.dictionaries = null);
    });
  }
  open(t) {
    return D(this, void 0, void 0, function* () {
      return this.closed || (this.autoDestroy = la(this, t), this.schema || (this.schema = yield this._reader.readSchema()) || (yield this.cancel())), this;
    });
  }
  throw(t) {
    return D(this, void 0, void 0, function* () {
      return !this.closed && this.autoDestroy && (this.closed = !0) ? yield this.reset()._reader.throw(t) : P;
    });
  }
  return(t) {
    return D(this, void 0, void 0, function* () {
      return !this.closed && this.autoDestroy && (this.closed = !0) ? yield this.reset()._reader.return(t) : P;
    });
  }
  next() {
    return D(this, void 0, void 0, function* () {
      if (this.closed)
        return P;
      let t;
      const { _reader: e } = this;
      for (; t = yield this._readNextMessageAndValidate(); )
        if (t.isSchema())
          yield this.reset(t.header());
        else if (t.isRecordBatch()) {
          this._recordBatchIndex++;
          const i = t.header(), s = yield e.readMessageBody(t.bodyLength);
          return { done: !1, value: this._loadRecordBatch(i, s) };
        } else if (t.isDictionaryBatch()) {
          this._dictionaryIndex++;
          const i = t.header(), s = yield e.readMessageBody(t.bodyLength), r = this._loadDictionaryBatch(i, s);
          this.dictionaries.set(i.id, r);
        }
      return this.schema && this._recordBatchIndex === 0 ? (this._recordBatchIndex++, { done: !1, value: new ea(this.schema) }) : yield this.return();
    });
  }
  _readNextMessageAndValidate(t) {
    return D(this, void 0, void 0, function* () {
      return yield this._reader.readMessage(t);
    });
  }
}
class ca extends Rn {
  get footer() {
    return this._footer;
  }
  get numDictionaries() {
    return this._footer ? this._footer.numDictionaries : 0;
  }
  get numRecordBatches() {
    return this._footer ? this._footer.numRecordBatches : 0;
  }
  constructor(t, e) {
    super(t instanceof Ms ? t : new Ms(t), e);
  }
  isSync() {
    return !0;
  }
  isFile() {
    return !0;
  }
  open(t) {
    if (!this.closed && !this._footer) {
      this.schema = (this._footer = this._readFooter()).schema;
      for (const e of this._footer.dictionaryBatches())
        e && this._readDictionaryBatch(this._dictionaryIndex++);
    }
    return super.open(t);
  }
  readRecordBatch(t) {
    var e;
    if (this.closed)
      return null;
    this._footer || this.open();
    const i = (e = this._footer) === null || e === void 0 ? void 0 : e.getRecordBatch(t);
    if (i && this._handle.seek(i.offset)) {
      const s = this._reader.readMessage(x.RecordBatch);
      if (s?.isRecordBatch()) {
        const r = s.header(), o = this._reader.readMessageBody(s.bodyLength);
        return this._loadRecordBatch(r, o);
      }
    }
    return null;
  }
  _readDictionaryBatch(t) {
    var e;
    const i = (e = this._footer) === null || e === void 0 ? void 0 : e.getDictionaryBatch(t);
    if (i && this._handle.seek(i.offset)) {
      const s = this._reader.readMessage(x.DictionaryBatch);
      if (s?.isDictionaryBatch()) {
        const r = s.header(), o = this._reader.readMessageBody(s.bodyLength), a = this._loadDictionaryBatch(r, o);
        this.dictionaries.set(r.id, a);
      }
    }
  }
  _readFooter() {
    const { _handle: t } = this, e = t.size - ra, i = t.readInt32(e), s = t.readAt(e - i, i);
    return Ji.decode(s);
  }
  _readNextMessageAndValidate(t) {
    var e;
    if (this._footer || this.open(), this._footer && this._recordBatchIndex < this.numRecordBatches) {
      const i = (e = this._footer) === null || e === void 0 ? void 0 : e.getRecordBatch(this._recordBatchIndex);
      if (i && this._handle.seek(i.offset))
        return this._reader.readMessage(t);
    }
    return null;
  }
}
class cu extends zn {
  get footer() {
    return this._footer;
  }
  get numDictionaries() {
    return this._footer ? this._footer.numDictionaries : 0;
  }
  get numRecordBatches() {
    return this._footer ? this._footer.numRecordBatches : 0;
  }
  constructor(t, ...e) {
    const i = typeof e[0] != "number" ? e.shift() : void 0, s = e[0] instanceof Map ? e.shift() : void 0;
    super(t instanceof Un ? t : new Un(t, i), s);
  }
  isFile() {
    return !0;
  }
  isAsync() {
    return !0;
  }
  open(t) {
    const e = Object.create(null, {
      open: { get: () => super.open }
    });
    return D(this, void 0, void 0, function* () {
      if (!this.closed && !this._footer) {
        this.schema = (this._footer = yield this._readFooter()).schema;
        for (const i of this._footer.dictionaryBatches())
          i && (yield this._readDictionaryBatch(this._dictionaryIndex++));
      }
      return yield e.open.call(this, t);
    });
  }
  readRecordBatch(t) {
    return D(this, void 0, void 0, function* () {
      var e;
      if (this.closed)
        return null;
      this._footer || (yield this.open());
      const i = (e = this._footer) === null || e === void 0 ? void 0 : e.getRecordBatch(t);
      if (i && (yield this._handle.seek(i.offset))) {
        const s = yield this._reader.readMessage(x.RecordBatch);
        if (s?.isRecordBatch()) {
          const r = s.header(), o = yield this._reader.readMessageBody(s.bodyLength);
          return this._loadRecordBatch(r, o);
        }
      }
      return null;
    });
  }
  _readDictionaryBatch(t) {
    return D(this, void 0, void 0, function* () {
      var e;
      const i = (e = this._footer) === null || e === void 0 ? void 0 : e.getDictionaryBatch(t);
      if (i && (yield this._handle.seek(i.offset))) {
        const s = yield this._reader.readMessage(x.DictionaryBatch);
        if (s?.isDictionaryBatch()) {
          const r = s.header(), o = yield this._reader.readMessageBody(s.bodyLength), a = this._loadDictionaryBatch(r, o);
          this.dictionaries.set(r.id, a);
        }
      }
    });
  }
  _readFooter() {
    return D(this, void 0, void 0, function* () {
      const { _handle: t } = this;
      t._pending && (yield t._pending);
      const e = t.size - ra, i = yield t.readInt32(e), s = yield t.readAt(e - i, i);
      return Ji.decode(s);
    });
  }
  _readNextMessageAndValidate(t) {
    return D(this, void 0, void 0, function* () {
      if (this._footer || (yield this.open()), this._footer && this._recordBatchIndex < this.numRecordBatches) {
        const e = this._footer.getRecordBatch(this._recordBatchIndex);
        if (e && (yield this._handle.seek(e.offset)))
          return yield this._reader.readMessage(t);
      }
      return null;
    });
  }
}
class lu extends Rn {
  constructor(t, e) {
    super(t, e);
  }
  _loadVectors(t, e, i) {
    return new yl(e, t.nodes, t.buffers, this.dictionaries, this.schema.metadataVersion).visitMany(i);
  }
}
function la(n, t) {
  return t && typeof t.autoDestroy == "boolean" ? t.autoDestroy : n.autoDestroy;
}
function* Ls(n) {
  const t = Ut.from(n);
  try {
    if (!t.open({ autoDestroy: !1 }).closed)
      do
        yield t;
      while (!t.reset().open().closed);
  } finally {
    t.cancel();
  }
}
function Us(n) {
  return Dt(this, arguments, function* () {
    const e = yield F(Ut.from(n));
    try {
      if (!(yield F(e.open({ autoDestroy: !1 }))).closed)
        do
          yield yield F(e);
        while (!(yield F(e.reset().open())).closed);
    } finally {
      yield F(e.cancel());
    }
  });
}
function uu(n) {
  return new En(new lu(n));
}
function du(n) {
  const t = n.peek(rn + 7 & -8);
  return t && t.byteLength >= 4 ? ns(t) ? new oa(new ca(n.read())) : new En(new Rn(n)) : new En(new Rn((function* () {
  })()));
}
function hu(n) {
  return D(this, void 0, void 0, function* () {
    const t = yield n.peek(rn + 7 & -8);
    return t && t.byteLength >= 4 ? ns(t) ? new oa(new ca(yield n.read())) : new Vn(new zn(n)) : new Vn(new zn((function() {
      return Dt(this, arguments, function* () {
      });
    })()));
  });
}
function fu(n) {
  return D(this, void 0, void 0, function* () {
    const { size: t } = yield n.stat(), e = new Un(n, t);
    return t >= tu && ns(yield e.readAt(0, rn + 7 & -8)) ? new au(new cu(e)) : new Vn(new zn(e));
  });
}
function ua(n) {
  const t = Ut.from(n);
  return Pe(t) ? t.then((e) => ua(e)) : t.isAsync() ? t.readAll().then((e) => new rt(e)) : new rt(t.readAll());
}
const si = Zl({
  id: he([1, 2, 3, 4, 5], new Yt()),
  name: he(["alpha", "beta", "gamma", "delta", "epsilon"]),
  value: he([10.5, 22.3, 7.8, 99.1, 45], new jn()),
  active: he([!0, !1, !0, !0, !1]),
  category: he(["A", "B", "A", "C", "B"])
});
function pu(n, t = 100) {
  const i = n.schema.fields.map((c) => c.name), s = `<thead><tr>${i.map((c) => `<th>${kn(String(c))}</th>`).join("")}</tr></thead>`, r = Math.min(n.numRows, t), o = [];
  for (let c = 0; c < r; c++) {
    const u = i.map((d) => {
      const h = n.getChild(d)?.get(c);
      return `<td>${kn(yu(h))}</td>`;
    });
    o.push(`<tr>${u.join("")}</tr>`);
  }
  const a = `<tbody>${o.join("")}</tbody>`;
  return `<table>${s}${a}</table>`;
}
function yu(n) {
  return n == null ? "" : typeof n == "boolean" ? n ? "true" : "false" : typeof n == "bigint" ? n.toString() : n instanceof Date ? n.toISOString() : typeof n == "object" ? JSON.stringify(n) : String(n);
}
function kn(n) {
  return n.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}
const gu = (
  /* css */
  `
  :host {
    display: block;
    box-sizing: border-box;
    font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
    font-size: 13px;
    color: var(--nubi-fg, #e2e8f0);
    background: var(--nubi-bg, #0f1117);
    border: 1px solid var(--nubi-border, #2d3748);
    border-radius: 8px;
    overflow: hidden;
  }

  .nubi-wrap {
    width: 100%;
    height: 100%;
    overflow: auto;
    position: relative;
  }

  .nubi-toolbar {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 12px;
    background: var(--nubi-accent, #1e2433);
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    font-size: 11px;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.8;
    gap: 8px;
  }

  .nubi-toolbar .nubi-title {
    font-weight: 600;
    letter-spacing: 0.02em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
    flex: 1;
  }

  .nubi-badge {
    font-size: 10px;
    padding: 2px 6px;
    border-radius: 4px;
    font-weight: 600;
    letter-spacing: 0.04em;
    white-space: nowrap;
    flex-shrink: 0;
  }

  .nubi-badge.hit  { background: #064e3b; color: #6ee7b7; }
  .nubi-badge.miss { background: #1e3a5f; color: #93c5fd; }
  .nubi-badge.sample {
    background: #422006;
    color: #fed7aa;
  }

  .nubi-sample-note {
    font-size: 11px;
    color: #f97316;
    padding: 4px 12px 6px;
    background: #1a1208;
    border-bottom: 1px solid #7c2d12;
    text-align: center;
  }

  .nubi-table-wrap {
    overflow: auto;
    max-height: calc(100% - 72px);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12px;
    line-height: 1.4;
  }

  thead tr {
    background: var(--nubi-accent, #1e2433);
    position: sticky;
    top: 0;
    z-index: 1;
  }

  thead th {
    padding: 7px 10px;
    text-align: left;
    font-weight: 600;
    font-size: 11px;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.7;
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    white-space: nowrap;
  }

  tbody tr {
    border-bottom: 1px solid var(--nubi-border, #2d3748);
    transition: background 0.1s;
  }

  tbody tr:hover {
    background: rgba(255, 255, 255, 0.04);
  }

  tbody td {
    padding: 6px 10px;
    color: var(--nubi-fg, #e2e8f0);
    font-variant-numeric: tabular-nums;
    white-space: nowrap;
    max-width: 260px;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .nubi-loading {
    padding: 32px;
    text-align: center;
    color: var(--nubi-fg, #e2e8f0);
    opacity: 0.5;
  }

  .nubi-loading::after {
    content: '';
    display: inline-block;
    width: 14px;
    height: 14px;
    border: 2px solid currentColor;
    border-top-color: transparent;
    border-radius: 50%;
    vertical-align: -3px;
    margin-left: 8px;
    animation: nubi-spin 0.8s linear infinite;
  }

  @keyframes nubi-spin {
    to { transform: rotate(360deg); }
  }

  .nubi-error-msg {
    padding: 16px;
    color: #f87171;
    font-size: 12px;
    background: #1c0a0a;
    border-radius: 4px;
    margin: 8px;
  }

  .nubi-footer {
    display: flex;
    align-items: center;
    justify-content: flex-end;
    padding: 4px 10px;
    font-size: 10px;
    opacity: 0.45;
    border-top: 1px solid var(--nubi-border, #2d3748);
    gap: 8px;
  }
`
);
class mu extends HTMLElement {
  // ---- Custom element lifecycle ------------------------------------------
  static get observedAttributes() {
    return ["query", "token", "get-token", "backend", "theme"];
  }
  constructor() {
    super(), this._shadow = this.attachShadow({ mode: "open" }), this._abortController = null, this._rendering = !1;
  }
  connectedCallback() {
    this._render();
  }
  disconnectedCallback() {
    this._abort();
  }
  attributeChangedCallback(t, e, i) {
    e !== i && this.isConnected && this._render();
  }
  // ---- Internal helpers --------------------------------------------------
  _abort() {
    this._abortController && (this._abortController.abort(), this._abortController = null);
  }
  /**
   * Resolve a JWT token from:
   *  1. The `token` attribute (static string).
   *  2. The `get-token` attribute — a function name on `window`.
   *
   * @returns {Promise<string | null>}
   */
  async _resolveToken() {
    const t = this.getAttribute("token");
    if (t) return t;
    const e = this.getAttribute("get-token");
    if (!e) return null;
    const i = window[e];
    if (typeof i != "function")
      return console.warn(`[nubi-dashboard] window.${e} is not a function`), null;
    try {
      return await i() ?? null;
    } catch (s) {
      return console.warn("[nubi-dashboard] getToken() threw:", s.message), null;
    }
  }
  /** @returns {string} */
  _backendUrl() {
    return (this.getAttribute("backend") || "http://localhost:8000").replace(/\/$/, "");
  }
  // ---- DOM helpers -------------------------------------------------------
  /** Show the loading spinner. */
  _showLoading() {
    const t = this._shadow.querySelector(".nubi-table-wrap");
    t && (t.innerHTML = '<div class="nubi-loading">Running query</div>');
    const e = this._shadow.querySelector(".nubi-sample-note");
    e && (e.style.display = "none");
  }
  /** Render table data into the shadow DOM. */
  _showTable(t, { cacheStatus: e = "MISS", elapsedMs: i = 0, isSample: s = !1 } = {}) {
    const r = this._shadow.querySelector(".nubi-badge");
    r && (s ? (r.textContent = "SAMPLE", r.className = "nubi-badge sample") : e === "HIT" ? (r.textContent = "CACHE HIT", r.className = "nubi-badge hit") : (r.textContent = "LIVE", r.className = "nubi-badge miss"));
    const o = this._shadow.querySelector(".nubi-sample-note");
    o && (o.style.display = s ? "block" : "none");
    const a = this._shadow.querySelector(".nubi-table-wrap");
    a && (a.innerHTML = pu(t, 100));
    const c = this._shadow.querySelector(".nubi-footer");
    c && (c.textContent = `${t.numRows.toLocaleString()} row${t.numRows !== 1 ? "s" : ""} · ${i}ms`);
  }
  /** Show an error message (only used as last resort; usually we fall to sample). */
  _showError(t) {
    const e = this._shadow.querySelector(".nubi-table-wrap");
    e && (e.innerHTML = `<div class="nubi-error-msg">Error: ${kn(t)}</div>`);
  }
  // ---- Shadow DOM scaffold -----------------------------------------------
  _ensureScaffold() {
    if (this._shadow.querySelector(".nubi-wrap")) return;
    const t = document.createElement("style");
    t.textContent = gu;
    const e = this.getAttribute("query") || "Query", i = e.length > 60 ? e.slice(0, 57) + "…" : e;
    this._shadow.innerHTML = "", this._shadow.appendChild(t), this._shadow.innerHTML += /* html */
    `
      <div class="nubi-wrap">
        <div class="nubi-toolbar">
          <span class="nubi-title">${kn(i)}</span>
          <span class="nubi-badge miss">…</span>
        </div>
        <div class="nubi-sample-note" style="display:none">
          preview (sample data) — connect a backend to load real results
        </div>
        <div class="nubi-table-wrap">
          <div class="nubi-loading">Running query</div>
        </div>
        <div class="nubi-footer"></div>
      </div>
    `, this._shadow.insertBefore(t, this._shadow.firstChild);
  }
  // ---- Core render -------------------------------------------------------
  async _render() {
    this._abort();
    const t = new AbortController();
    this._abortController = t, this._rendering = !0, this._ensureScaffold(), this._showLoading();
    const e = performance.now(), i = this.getAttribute("query") || "", s = this._backendUrl();
    let r;
    try {
      r = await this._resolveToken();
    } catch {
      r = null;
    }
    if (t.signal.aborted) return;
    if (i && s)
      try {
        const a = {
          "Content-Type": "application/json",
          Accept: "application/vnd.apache.arrow.stream"
        };
        r && (a.Authorization = `Bearer ${r}`);
        const c = await fetch(`${s}/api/v1/query`, {
          method: "POST",
          headers: a,
          body: JSON.stringify({ sql: i }),
          // credentials: 'omit' — cross-origin embed; no cookies sent
          credentials: "omit",
          signal: t.signal
        });
        if (t.signal.aborted) return;
        if (c.ok) {
          const d = c.headers.get("X-Nubi-Cache") ?? "MISS", h = await c.arrayBuffer();
          if (t.signal.aborted) return;
          const T = ua(new Uint8Array(h)), O = Math.round(performance.now() - e);
          this._showTable(T, { cacheStatus: d, elapsedMs: O, isSample: !1 }), this.dispatchEvent(new CustomEvent("nubi:query-run", {
            bubbles: !0,
            composed: !0,
            detail: { rowCount: T.numRows, cacheStatus: d, elapsedMs: O, sample: !1 }
          })), this.dispatchEvent(new CustomEvent("nubi:ready", {
            bubbles: !0,
            composed: !0,
            detail: { rowCount: T.numRows }
          })), this._rendering = !1;
          return;
        }
        const u = `Query API returned HTTP ${c.status}`;
        console.warn(`[nubi-dashboard] ${u} — showing sample`), this.dispatchEvent(new CustomEvent("nubi:error", {
          bubbles: !0,
          composed: !0,
          detail: { message: u }
        }));
      } catch (a) {
        if (a.name === "AbortError") return;
        console.warn("[nubi-dashboard] Fetch/parse error — showing sample:", a.message), this.dispatchEvent(new CustomEvent("nubi:error", {
          bubbles: !0,
          composed: !0,
          detail: { message: a.message }
        }));
      }
    if (t.signal.aborted) return;
    const o = Math.round(performance.now() - e);
    this._showTable(si, { cacheStatus: "SAMPLE", elapsedMs: o, isSample: !0 }), this.dispatchEvent(new CustomEvent("nubi:query-run", {
      bubbles: !0,
      composed: !0,
      detail: { rowCount: si.numRows, cacheStatus: "SAMPLE", elapsedMs: o, sample: !0 }
    })), this.dispatchEvent(new CustomEvent("nubi:ready", {
      bubbles: !0,
      composed: !0,
      detail: { rowCount: si.numRows }
    })), this._rendering = !1;
  }
}
customElements.define("nubi-dashboard", mu);
export {
  mu as NubiDashboard
};
//# sourceMappingURL=nubi-dashboard.es.js.map
